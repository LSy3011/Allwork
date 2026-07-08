#include "neg_op.h"
#include "neg_op_constants.h"
#include "neg_op_compute_inst.h"
#include "neg_op_rtovr_inst.h"
#include "neg_op_ur_inst.h"
#include <iostream>
#include <iomanip>
#include <cmath>
#include <cstring>
#include <algorithm>
#include <set>
#include <map>
#include <sstream>
#include <stdexcept>
#include "../../iq/iq_id_defs.h"

namespace aiacc {
namespace op {

namespace {
constexpr uint8_t kNumUrBanks = 8;
constexpr uint32_t kUrBankDepth = 256;

constexpr uint8_t RTOVR_OUT_DVR_FPADD_S0  = 5;   // FPADD src0
constexpr uint8_t RTOVR_OUT_DVR_FPADD_S1  = 6;   // FPADD src1
constexpr uint8_t RTOVR_OUT_UVR_WDATA_0   = 35;  // UR 写端口 0

constexpr uint32_t FPADD_LATENCY = 4;
constexpr uint32_t CYCLES_PER_ITERATION = 8;

inline bool ranges_overlap(uint32_t a_start, uint32_t a_len,
                           uint32_t b_start, uint32_t b_len) {
    const uint32_t a_end = a_start + a_len;
    const uint32_t b_end = b_start + b_len;
    return (a_start < b_end) && (b_start < a_end);
}
} // anonymous namespace

// ==========================================
// OpBase 接口
// ==========================================
std::string NegOp::get_name() const { return "neg"; }

NegOpParams NegOp::sanitize_params(const NegOpParams& in_params,
                                   std::vector<std::string>* notes) const {
    NegOpParams params = in_params;
    const uint32_t total_elems = params.para_n * params.para_c * params.para_h * params.para_w;
    const uint32_t epw = params.get_elems_per_word();
    const uint32_t total_steps = (total_elems + epw - 1) / epw;

    auto add_note = [&](const std::string& s) { if (notes) notes->push_back(s); };

    // 1. 规整 Bank 编号
    params.para_input_bank &= 0x7;
    params.para_zero_bank  &= 0x7;
    params.para_output_bank &= 0x7;

    // 2. 避免 Input 与 Zero 同拍同 Bank 读取冲突 (RTL 端口限制)
    if (params.para_input_bank == params.para_zero_bank) {
        uint8_t old_bank = params.para_zero_bank;
        uint8_t new_bank = (params.para_input_bank + 1) & 0x7;
        if (new_bank == params.para_output_bank) new_bank = (new_bank + 1) & 0x7;
        params.para_zero_bank = new_bank;
        std::ostringstream oss;
        oss << "Remap Zero bank from B" << (int)old_bank << " to B" << (int)new_bank << " to avoid conflict with Input";
        add_note(oss.str());
    }

    // 3. 保留 input/output 佈局（支援 in-place: output 與 input 同 bank/addr）。
    // 只在 zero 與 output 區間重疊時重映射 zero，避免常量 0 被覆蓋。
    if (params.para_zero_bank == params.para_output_bank &&
        ranges_overlap(params.para_zero_addr, 1, params.para_output_addr, total_steps)) {
        uint8_t old_bank = params.para_zero_bank;
        for (uint8_t b = 0; b < kNumUrBanks; ++b) {
            if (b != params.para_input_bank && b != params.para_output_bank) {
                params.para_zero_bank = b;
                break;
            }
        }
        std::ostringstream oss;
        oss << "Remap Zero bank from B" << (int)old_bank << " to B" << (int)params.para_zero_bank
            << " to avoid overlap with output range";
        add_note(oss.str());
    }

    return params;
}

OpInstructionsBase* NegOp::compile(const app::AppOperatorRequest& req) {
    NegOpParams params;
    params.para_n = req.para_dimensions.para_n;
    params.para_c = req.para_dimensions.para_c;
    params.para_h = req.para_dimensions.para_h;
    params.para_w = req.para_dimensions.para_w;
    params.para_dtype = (req.para_inputs.count("input_tensor") && 
                         req.para_inputs.at("input_tensor").para_dtype == app::AppDataType::FLOAT16) ? 1 : 0;

    auto parse_i = [&](const char* name, int& v) {
        auto it = req.para_parameters.find(name);
        if (it != req.para_parameters.end() && !it->second.para_data_i32.empty())
            v = it->second.para_data_i32[0];
    };
    int b;
    b=0; parse_i("input_bank", b); params.para_input_bank = (uint8_t)b;
    b=0; parse_i("input_addr", b); params.para_input_addr = (uint32_t)b;
    b=1; parse_i("zero_bank", b);  if (req.para_parameters.count("other_bank")) parse_i("other_bank", b); params.para_zero_bank = (uint8_t)b;
    b=0; parse_i("zero_addr", b);  params.para_zero_addr = (uint32_t)b;
    b=2; parse_i("output_bank", b); params.para_output_bank = (uint8_t)b;
    b=0; parse_i("output_addr", b); params.para_output_addr = (uint32_t)b;

    NegOpInstructions* insts = new NegOpInstructions();
    *insts = compile(params);
    return insts;
}

NegOpInstructions NegOp::compile(const NegOpParams& raw_params) {
    std::vector<std::string> notes;
    NegOpParams params = sanitize_params(raw_params, &notes);
    NegOpInstructions result;

    const uint32_t total_elems = params.para_n * params.para_c * params.para_h * params.para_w;
    const uint epw = params.get_elems_per_word();
    const uint32_t total_steps = (total_elems + epw - 1) / epw;

    result.para_total_steps = total_steps;
    result.para_total_elements = total_elems;

    std::cout << "[Neg] Steps: " << total_steps << ", Elem: " << total_elems << std::endl;
    for(const auto& n : notes) std::cout << "  [Layout] " << n << std::endl;

    // FPADD(SUB) 编码
    result.para_fpadd_code = neg_get_fpadd_ctrl(params.para_dtype);

    uint32_t timeline_cursor = 0;
    std::set<uint32_t> write_cycles;

    constexpr uint32_t kReadToAddLat = 2;   // UR read -> FPADD fire
    constexpr uint32_t kAddToRouteLat = 4;  // FPADD fire -> DR_FPADD_D valid
    constexpr uint32_t kRouteToWriteLat = 1; // RtoVR route -> UR write

    for (uint32_t s = 0; s < total_steps; ++s) {
        while (true) {
            uint32_t B = timeline_cursor;
            bool conflict = false;
            if (params.para_input_bank == params.para_output_bank ||
                params.para_zero_bank  == params.para_output_bank) {
                if (write_cycles.count(B)) conflict = true;
            }
            if (!conflict) break;
            timeline_cursor++;
        }

        const uint32_t B = timeline_cursor;
        // 记录写周期：B + 2 + 4 + 1 = B + 7
        write_cycles.insert(B + kReadToAddLat + kAddToRouteLat + kRouteToWriteLat);

        // B+0: Read
        result.sched_ur_r0.push_back({B+0, neg_encode_ur_read(params.para_input_bank, params.para_input_addr + s)});
        result.sched_ur_r1.push_back({B+0, neg_encode_ur_read(params.para_zero_bank, params.para_zero_addr)});

        // B+1: RtoVR to FPADD (Ports 5, 6) -> ID 25
        // FPADD subtraction is S0 - S1. Route zero to S0 and input to S1 => 0 - x = -x.
        result.sched_rtovr_5.push_back({B+1, neg_encode_rtovr_source(RTOVR_IN_UR_RDATA_1)}); // Zero  -> S0
        result.sched_rtovr_6.push_back({B+1, neg_encode_rtovr_source(RTOVR_IN_UR_RDATA_0)}); // Input -> S1

        // B+2: FPADD Fire (S0 - S1 = 0 - Input = -Input)
        result.sched_fpadd.push_back({B+2, result.para_fpadd_code});

        // B+6: RtoVR to UR (Port 35) -> ID 26
        result.sched_rtovr_35.push_back({B + kReadToAddLat + kAddToRouteLat, neg_encode_rtovr_source(RTOVR_IN_DR_FPADD_D)});

        // B+7: Write Output
        result.sched_ur_w0.push_back({B + kReadToAddLat + kAddToRouteLat + kRouteToWriteLat,
                                      neg_encode_ur_write(params.para_output_bank, params.para_output_addr + s)});

        timeline_cursor += 1;
    }
    result.para_total_cycles = timeline_cursor + 10;

    return result;
}

// -----------------------------------------------------------------------------
// 指令打包逻辑
// -----------------------------------------------------------------------------
namespace {
static uint64_t serialize_header_41(uint8_t vld, uint8_t jump, uint8_t dly) {
    uint64_t bits = 0;
    uint32_t pos = 0;
    pos += 6; pos += 4; // inc_embed, inc
    bits |= (uint64_t)(dly & 0x7) << pos; pos += 3;
    pos += 7; pos += 10; pos += 6; pos += 3;
    bits |= (uint64_t)(jump & 0x1) << pos; pos += 1;
    bits |= (uint64_t)(vld & 0x1) << pos;
    return bits;
}

static std::vector<uint8_t> pack_instruction(uint8_t vld, uint8_t jump, uint8_t dly, uint32_t code, uint32_t code_bits) {
    uint64_t header = serialize_header_41(vld, jump, dly);
    uint32_t total_bits = 41 + code_bits;
    uint32_t num_bytes = (total_bits + 7) / 8;
    unsigned __int128 tv = (unsigned __int128)header << code_bits | (code & ((1u << code_bits) - 1u));
    std::vector<uint8_t> bytes;
    for (int i = (int)num_bytes - 1; i >= 0; --i) bytes.push_back((uint8_t)((tv >> (i * 8)) & 0xFF));
    return bytes;
}

static std::vector<uint8_t> sched_to_iq_bytes(const std::vector<SchedEntry>& sched, uint32_t code_bits) {
    std::vector<uint8_t> data;
    int32_t prev_cycle = -1;
    for (const auto& e : sched) {
        int32_t target_dly = (prev_cycle < 0) ? (int32_t)e.abs_cycle : (int32_t)e.abs_cycle - prev_cycle - 1;
        while (target_dly > 7) {
            auto nop = pack_instruction(1, 0, 7, 0, code_bits);
            data.insert(data.end(), nop.begin(), nop.end());
            target_dly -= 8;
        }
        auto inst = pack_instruction(1, 0, (uint8_t)target_dly, e.code, code_bits);
        data.insert(data.end(), inst.begin(), inst.end());
        prev_cycle = (int32_t)e.abs_cycle;
    }
    auto end_inst = pack_instruction(1, 1, 0, 0, code_bits);
    data.insert(data.end(), end_inst.begin(), end_inst.end());
    return data;
}
} // anonymous namespace

std::vector<IqInstructionFile> NegOpInstructions::get_iq_files() const {
    std::vector<IqInstructionFile> files;
    auto add = [&](const std::vector<SchedEntry>& s, uint32_t id, const std::string& name, uint32_t bits) {
        if (s.empty()) return;
        IqInstructionFile f; f.para_iq_id = id; f.para_iq_name = name; f.para_width_bits = 41 + bits;
        f.para_data = sched_to_iq_bytes(s, bits);
        files.push_back(f);
    };
    add(sched_ur_r0,  iq::IQ_ID_UR_RDATA_0, iq::IQ_NAME_UR_RDATA_0, 12);
    add(sched_ur_r1,  iq::IQ_ID_UR_RDATA_1, iq::IQ_NAME_UR_RDATA_1, 12);
    add(sched_ur_w0,  iq::IQ_ID_UR_WDATA_0, iq::IQ_NAME_UR_WDATA_0, 28);
    add(sched_fpadd,  iq::IQ_ID_FPADD,      iq::IQ_NAME_FPADD,       5);
    // Keep NEG output naming compatible with existing RTL deploy scripts:
    //   iq_24_rtovr-5.hex / iq_24_rtovr-6.hex / iq_24_rtovr-35.hex
    add(sched_rtovr_5,  iq::IQ_ID_RTOVR_BASE, "rtovr-5",  7);
    add(sched_rtovr_6,  iq::IQ_ID_RTOVR_BASE, "rtovr-6",  7);
    add(sched_rtovr_35, iq::IQ_ID_RTOVR_BASE, "rtovr-35", 7);
    return files;
}

std::vector<uint8_t> NegOpInstructions::serialize() const {
    std::vector<uint8_t> res;
    auto p32 = [&](uint32_t v) { for(int i=0;i<4;++i) res.push_back((v>>(i*8))&0xFF); };
    p32(para_total_steps); p32(para_total_elements); p32(0); p32(para_total_cycles);
    res.push_back(0); // fpmul_code
    res.push_back(para_fpadd_code);

    auto ser_sched = [&](const std::vector<SchedEntry>& s) {
        p32((uint32_t)s.size());
        for (const auto& e : s) { p32(e.abs_cycle); p32(e.code); }
    };
    ser_sched(sched_ur_r0); ser_sched(sched_ur_r1); 
    ser_sched({}); // ur_r2
    ser_sched(sched_ur_w0); 
    ser_sched({}); // fpmul
    ser_sched(sched_fpadd);
    return res;
}


// -----------------------------------------------------------------------------
// Golden & Data Gen
// -----------------------------------------------------------------------------
bool NegOp::calculate_golden(const app::AppOperatorRequest& req, std::vector<uint8_t>& golden_bin) {
    golden_bin.clear();
    const auto& in = req.para_inputs.at("input_tensor");
    for (float v : in.para_data_f32) {
        if (in.para_dtype == app::AppDataType::FLOAT16) {
            uint16_t h = app::float32_to_float16(v); 
            h ^= 0x8000; 
            golden_bin.push_back(h & 0xFF);
            golden_bin.push_back((h >> 8) & 0xFF);
        } else {
            float r = -v;
            uint32_t b; memcpy(&b, &r, 4);
            for(int i=0;i<4;++i) golden_bin.push_back((b>>(i*8))&0xFF);
        }
    }
    return true;
}

bool NegOp::generate_data(const app::AppOperatorRequest& req,
                          std::vector<uint8_t>& input_bin,
                          std::vector<uint8_t>& other_bin,
                          std::vector<uint8_t>& alpha_bin) {
    input_bin.clear(); other_bin.clear(); alpha_bin.clear();
    auto write_v = [&](std::vector<uint8_t>& b, const app::AppTensorDesc& t) {
        if (t.para_dtype == app::AppDataType::FLOAT32) {
            for(float f : t.para_data_f32) { uint32_t x; memcpy(&x,&f,4); for(int i=0;i<4;++i) b.push_back((x>>(i*8))&0xFF); }
        } else {
            for(float f : t.para_data_f32) { uint16_t x = app::float32_to_float16(f); b.push_back(x&0xFF); b.push_back(x>>8); }
        }
    };
    if (req.para_inputs.count("input_tensor")) write_v(input_bin, req.para_inputs.at("input_tensor"));
    if (req.para_parameters.count("zero_tensor")) write_v(other_bin, req.para_parameters.at("zero_tensor"));
    return true;
}

void NegOp::fill_random_request(app::AppOperatorRequest& req, std::mt19937& rng, std::uniform_real_distribution<float>& dist, const std::string& dtype_str) {
    req.para_dimensions.para_n = 1;
    req.para_dimensions.para_c = 1;
    req.para_dimensions.para_h = 1;
    req.para_dimensions.para_w = 32;

    app::AppTensorDesc in;
    in.para_name = "input_tensor";
    in.para_dtype = (dtype_str == "fp16") ? app::AppDataType::FLOAT16 : app::AppDataType::FLOAT32;
    in.para_shape = {1, 1, 1, 32};
    for (int i=0; i<32; ++i) in.para_data_f32.push_back(dist(rng));
    req.para_inputs["input_tensor"] = in;

    app::AppTensorDesc zero;
    zero.para_name = "zero_tensor";
    zero.para_dtype = in.para_dtype;
    zero.para_shape = {1, 1, 1, (uint32_t)(dtype_str == "fp16" ? 32 : 16)};
    for (int i=0; i<(int)zero.para_shape.back(); ++i) zero.para_data_f32.push_back(0.0f);
    req.para_parameters["zero_tensor"] = zero;

    auto get_p = [&](const char* n, int defv) {
        auto it = req.para_parameters.find(n);
        if (it != req.para_parameters.end() && !it->second.para_data_i32.empty()) {
            return it->second.para_data_i32[0];
        }
        return defv;
    };
    auto set_p = [&](const char* n, int v) {
        app::AppTensorDesc t; t.para_dtype = app::AppDataType::INT32; t.para_data_i32.push_back(v); req.para_parameters[n] = t;
    };

    // Keep externally provided bank overrides (CLI/tvgen_core bank_conflict) instead of hardcoding 0/1/2.
    const int ib = get_p("input_bank",  (req.para_bc_input_bank  >= 0) ? req.para_bc_input_bank  : 0);
    const int ob = get_p("other_bank",  (req.para_bc_other_bank  >= 0) ? req.para_bc_other_bank  : 1);
    const int xb = get_p("output_bank", (req.para_bc_output_bank >= 0) ? req.para_bc_output_bank : 2);
    const int ia = get_p("input_addr", 0);
    const int oa = get_p("zero_addr", 0);
    const int xa = get_p("output_addr", 0);

    set_p("input_bank", ib);  set_p("input_addr", ia);
    set_p("other_bank", ob);  // keep compatibility with tvgen/core overrides
    set_p("zero_bank",  ob);  set_p("zero_addr", oa);
    set_p("output_bank", xb); set_p("output_addr", xa);

    // Aliases for TV generator compatibility
    req.para_bc_input_bank = ib;
    req.para_bc_other_bank = ob;
    req.para_bc_output_bank = xb;
}

REGISTER_OP(NegOp);

} // namespace op
} // namespace aiacc
