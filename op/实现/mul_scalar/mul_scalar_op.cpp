#include "mul_scalar_op.h"

#include <algorithm>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <stdexcept>

#include "../../app/fp16_utils.h"
#include "../../iq/iq_id_defs.h"
#include "../common/lsu_emit.h"
#include "mul_scalar_op_compute_inst.h"
#include "mul_scalar_op_constants.h"
#include "mul_scalar_op_rtovr_inst.h"
#include "mul_scalar_op_ur_inst.h"

namespace aiacc {
namespace op {

namespace {

constexpr uint8_t kMaxDlyField = 7;
constexpr uint32_t kUrWordBytes = 16;
constexpr uint64_t kDefaultDdrIntlvAddr = 0x10000000ULL;

template <typename EmitDummyFn>
uint8_t emit_dummy_for_long_delay(uint32_t& total_dly, EmitDummyFn&& emit_dummy) {
    while (total_dly > kMaxDlyField) {
        emit_dummy();
        total_dly -= 8;
    }
    return static_cast<uint8_t>(total_dly);
}

float get_other_scalar(const app::AppOperatorRequest& req) {
    auto pit = req.para_parameters.find("other_scalar");
    if (pit != req.para_parameters.end()) {
        if (!pit->second.para_data_f32.empty()) {
            return pit->second.para_data_f32[0];
        }
        if (!pit->second.para_data_i32.empty()) {
            return static_cast<float>(pit->second.para_data_i32[0]);
        }
    }

    auto iit = req.para_inputs.find("other_tensor");
    if (iit != req.para_inputs.end() && !iit->second.para_data_f32.empty()) {
        return iit->second.para_data_f32[0];
    }
    return 1.0f;
}

uint64_t get_u64_param(const app::AppOperatorRequest& req,
                       const std::string& key,
                       uint64_t default_value) {
    auto it = req.para_parameters.find(key);
    if (it != req.para_parameters.end() && !it->second.para_data_i32.empty()) {
        return static_cast<uint64_t>(static_cast<uint32_t>(it->second.para_data_i32[0]));
    }
    return default_value;
}

bool is_ddr_layout(const app::AppOperatorRequest& req) {
    return req.para_layout_mode == "ddr" || req.para_layout_mode == "mul_scalar_ddr";
}

void append_u16_le(std::vector<uint8_t>& out, uint16_t bits) {
    out.push_back(bits & 0xFF);
    out.push_back((bits >> 8) & 0xFF);
}

void append_u16_be(std::vector<uint8_t>& out, uint16_t bits) {
    out.push_back((bits >> 8) & 0xFF);
    out.push_back(bits & 0xFF);
}

void append_u32_le(std::vector<uint8_t>& out, uint32_t bits) {
    out.push_back(bits & 0xFF);
    out.push_back((bits >> 8) & 0xFF);
    out.push_back((bits >> 16) & 0xFF);
    out.push_back((bits >> 24) & 0xFF);
}

void append_u32_be(std::vector<uint8_t>& out, uint32_t bits) {
    out.push_back((bits >> 24) & 0xFF);
    out.push_back((bits >> 16) & 0xFF);
    out.push_back((bits >> 8) & 0xFF);
    out.push_back(bits & 0xFF);
}

std::vector<std::pair<uint32_t, uint64_t>> make_lsu_header(uint8_t vld, uint8_t jump,
                                                           uint16_t loops,
                                                           uint8_t dly,
                                                           uint16_t inc) {
    return {
        {1, vld},
        {1, jump},
        {3, 0},
        {6, 0},
        {10, loops},
        {7, 0},
        {3, dly},
        {13, inc},
        {6, 0},
    };
}

std::vector<uint8_t> pack_msb_instruction(const std::vector<std::pair<uint32_t, uint64_t>>& fields) {
    uint32_t total_bits = 0;
    for (const auto& field : fields) total_bits += field.first;
    std::vector<uint8_t> bytes((total_bits + 7) / 8, 0);
    const uint32_t leading_pad_bits =
        static_cast<uint32_t>(bytes.size() * 8) - total_bits;
    uint32_t bit_pos = 0;
    for (const auto& field : fields) {
        const uint32_t width = field.first;
        const uint64_t value = field.second;
        for (uint32_t i = 0; i < width; ++i) {
            const uint32_t src_bit = width - 1 - i;
            if (((value >> src_bit) & 1ULL) == 0) continue;
            const uint32_t dst_bit = leading_pad_bits + bit_pos + i;
            bytes[dst_bit / 8] |= static_cast<uint8_t>(1u << (7 - (dst_bit % 8)));
        }
        bit_pos += width;
    }
    return bytes;
}

uint16_t encode_lsu_ur_addr(uint8_t bank, uint8_t addr, uint8_t byte_offset = 0) {
    return static_cast<uint16_t>(((bank & 0x7u) << 12) |
                                 ((addr & 0xFFu) << 4) |
                                 (byte_offset & 0xFu));
}

std::vector<uint8_t> make_lsu_end_instruction(uint32_t code_bits) {
    auto fields = make_lsu_header(1, 1, 0, 0, 0);
    fields.push_back({code_bits, 0});
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_lsu_idle_instruction(uint32_t code_bits) {
    auto fields = make_lsu_header(1, 0, 0, 0, 0);
    fields.push_back({code_bits, 0});
    return pack_msb_instruction(fields);
}

void append_repeat(std::vector<uint8_t>& dst,
                   const std::vector<uint8_t>& inst,
                   uint32_t count) {
    for (uint32_t i = 0; i < count; ++i) {
        dst.insert(dst.end(), inst.begin(), inst.end());
    }
}

LsuLoadParams make_mul_scalar_load_params(uint8_t smc_id_en,
                                          uint64_t gr_base_addr,
                                          uint64_t gr_intlv_addr,
                                          uint16_t ur_addr,
                                          uint16_t ldb_loops,
                                          uint16_t ldb_inc,
                                          uint16_t ldbaxi_loops,
                                          uint16_t ldbaxi_inc,
                                          uint8_t brst,
                                          uint8_t ur_wr_line_cnt = 1) {
    LsuLoadParams p{};
    p.para_gr_base_addr = gr_base_addr;
    p.para_gr_intlv_addr = gr_intlv_addr;
    p.para_brst = brst;
    p.para_tid = 0;
    p.para_ur_addr = ur_addr;
    p.para_ur_wr_line_cnt = ur_wr_line_cnt;
    p.para_line_byte_cnt = 0x0F;
    p.para_line_stride = 1;
    p.para_smc_id_en = smc_id_en;
    p.para_ldb_loops = ldb_loops;
    p.para_ldbaxi_loops = ldbaxi_loops;
    p.para_ldb_inc = ldb_inc;
    p.para_ldbaxi_inc = ldbaxi_inc;
    return p;
}

LsuStoreParams make_mul_scalar_store_params(uint8_t smc_id_en,
                                            uint64_t gr_base_addr,
                                            uint64_t gr_intlv_addr,
                                            uint16_t ur_addr,
                                            uint16_t stb_loops,
                                            uint16_t stb_inc,
                                            uint16_t stbaxi_loops,
                                            uint16_t stbaxi_inc,
                                            uint8_t brst,
                                            uint8_t ur_rd_line_cnt = 1) {
    LsuStoreParams p{};
    p.para_gr_base_addr = gr_base_addr;
    p.para_gr_intlv_addr = gr_intlv_addr;
    p.para_brst = brst;
    p.para_ur_addr = ur_addr;
    p.para_ur_rd_line_cnt = ur_rd_line_cnt;
    p.para_line_byte_cnt = 0x0F;
    p.para_line_stride = 1;
    p.para_tid = 2;
    p.para_smc_id_en = smc_id_en;
    p.para_stb_loops = stb_loops;
    p.para_stbaxi_loops = stbaxi_loops;
    p.para_stb_inc = stb_inc;
    p.para_stbaxi_inc = stbaxi_inc;
    return p;
}

uint32_t lsu_total_lines_from_loops(uint16_t lsu_loops) {
    return static_cast<uint32_t>(lsu_loops) + 1u;
}

void append_load_burst(IqInstructionFile& ldb_axi,
                       IqInstructionFile& ldb,
                       uint8_t smc_id_en,
                       uint64_t gr_base_addr,
                       uint64_t gr_intlv_addr,
                       uint16_t ur_addr,
                       uint32_t line_offset,
                       uint32_t line_count) {
    const uint8_t burst_lines = static_cast<uint8_t>(line_count);
    const uint16_t chunk_ur_addr =
        static_cast<uint16_t>(ur_addr + line_offset * kUrWordBytes);
    const uint64_t chunk_gr_base = gr_base_addr + line_offset * kUrWordBytes;
    // RTL实测：LDB 的 ur_wr_line_cnt>1 会提前 done 但不稳定落 UR。
    // 保持每拍写 1 行，靠 LDB loop 递增 UR；LDBAXI 用 burst 拉连续 DDR 行。
    const auto files = emit_load_iq_files(make_mul_scalar_load_params(
        smc_id_en,
        chunk_gr_base,
        gr_intlv_addr,
        chunk_ur_addr,
        static_cast<uint16_t>(burst_lines - 1u),
        kUrWordBytes,
        0,
        0,
        static_cast<uint8_t>(burst_lines - 1u),
        1));
    ldb_axi.para_data.insert(ldb_axi.para_data.end(),
                             files.at(0).para_data.begin(),
                             files.at(0).para_data.end());
    ldb.para_data.insert(ldb.para_data.end(),
                         files.at(1).para_data.begin(),
                         files.at(1).para_data.end());
}

void append_store_burst(IqInstructionFile& stb,
                        IqInstructionFile& stb_axi,
                        uint8_t smc_id_en,
                        uint64_t gr_base_addr,
                        uint64_t gr_intlv_addr,
                        uint16_t ur_addr,
                        uint32_t line_offset,
                        uint32_t line_count) {
    const uint8_t burst_lines = static_cast<uint8_t>(line_count);
    const uint16_t chunk_ur_addr =
        static_cast<uint16_t>(ur_addr + line_offset * kUrWordBytes);
    const uint64_t chunk_gr_base = gr_base_addr + line_offset * kUrWordBytes;
    // 与 LDB 对齐：STB 每拍读 1 行，AXI 侧按 burst 聚合写 DDR。
    const auto files = emit_store_iq_files(make_mul_scalar_store_params(
        smc_id_en,
        chunk_gr_base,
        gr_intlv_addr,
        chunk_ur_addr,
        static_cast<uint16_t>(burst_lines - 1u),
        kUrWordBytes,
        0,
        0,
        static_cast<uint8_t>(burst_lines - 1u),
        1));
    stb.para_data.insert(stb.para_data.end(),
                         files.at(0).para_data.begin(),
                         files.at(0).para_data.end());
    stb_axi.para_data.insert(stb_axi.para_data.end(),
                             files.at(1).para_data.begin(),
                             files.at(1).para_data.end());
}

std::vector<IqInstructionFile> mul_scalar_load(const MulScalarOpParams& params,
                                               uint8_t smc_id_en,
                                               uint16_t lsu_loops,
                                               uint16_t lsu_inc,
                                               uint16_t axi_loops,
                                               uint16_t axi_inc,
                                               uint8_t axi_brst) {
    (void)lsu_inc;
    (void)axi_loops;
    (void)axi_inc;
    (void)axi_brst;

    IqInstructionFile ldb_axi;
    ldb_axi.para_iq_id = iq::IQ_ID_LDBAXI;
    ldb_axi.para_iq_name = "ldb_axi";
    ldb_axi.para_width_bits = 50 + 104;

    IqInstructionFile ldb;
    ldb.para_iq_id = iq::IQ_ID_LDB;
    ldb.para_iq_name = iq::IQ_NAME_LDB;
    ldb.para_width_bits = 50 + 56;

    const uint16_t input_ur_addr =
        encode_lsu_ur_addr(params.para_input_bank, params.para_input_addr);
    const uint32_t total_lines = lsu_total_lines_from_loops(lsu_loops);
    for (uint32_t off = 0; off < total_lines; off += 16u) {
        const uint32_t lines = std::min<uint32_t>(16u, total_lines - off);
        append_load_burst(ldb_axi, ldb, smc_id_en,
                          params.para_input_ddr_base_addr,
                          params.para_ddr_intlv_addr,
                          input_ur_addr,
                          off,
                          lines);
    }

    append_load_burst(ldb_axi, ldb, smc_id_en,
                      params.para_other_ddr_base_addr,
                      params.para_ddr_intlv_addr,
                      encode_lsu_ur_addr(params.para_other_bank, params.para_other_addr),
                      0,
                      1);

    auto ldb_axi_end = make_lsu_end_instruction(104);
    ldb_axi.para_data.insert(ldb_axi.para_data.end(), ldb_axi_end.begin(), ldb_axi_end.end());

    auto ldb_end = make_lsu_end_instruction(56);
    ldb.para_data.insert(ldb.para_data.end(), ldb_end.begin(), ldb_end.end());

    std::vector<IqInstructionFile> files;
    files.push_back(std::move(ldb_axi));
    files.push_back(std::move(ldb));
    return files;
}

std::vector<IqInstructionFile> mul_scalar_identity_load(const MulScalarOpParams& params,
                                                        uint8_t smc_id_en,
                                                        uint16_t lsu_loops,
                                                        uint16_t lsu_inc,
                                                        uint16_t axi_loops,
                                                        uint16_t axi_inc,
                                                        uint8_t axi_brst) {
    (void)lsu_inc;
    (void)axi_loops;
    (void)axi_inc;
    (void)axi_brst;

    MulScalarOpParams load_params = params;
    load_params.para_input_bank = params.para_output_bank;
    load_params.para_input_addr = params.para_output_addr;

    IqInstructionFile ldb_axi;
    ldb_axi.para_iq_id = iq::IQ_ID_LDBAXI;
    ldb_axi.para_iq_name = "ldb_axi";
    ldb_axi.para_width_bits = 50 + 104;

    IqInstructionFile ldb;
    ldb.para_iq_id = iq::IQ_ID_LDB;
    ldb.para_iq_name = iq::IQ_NAME_LDB;
    ldb.para_width_bits = 50 + 56;

    const uint16_t input_ur_addr =
        encode_lsu_ur_addr(load_params.para_input_bank, load_params.para_input_addr);
    const uint32_t total_lines = lsu_total_lines_from_loops(lsu_loops);
    for (uint32_t off = 0; off < total_lines; off += 16u) {
        const uint32_t lines = std::min<uint32_t>(16u, total_lines - off);
        append_load_burst(ldb_axi, ldb, smc_id_en,
                          load_params.para_input_ddr_base_addr,
                          load_params.para_ddr_intlv_addr,
                          input_ur_addr,
                          off,
                          lines);
    }

    auto ldb_axi_end = make_lsu_end_instruction(104);
    ldb_axi.para_data.insert(ldb_axi.para_data.end(), ldb_axi_end.begin(), ldb_axi_end.end());

    auto ldb_end = make_lsu_end_instruction(56);
    ldb.para_data.insert(ldb.para_data.end(), ldb_end.begin(), ldb_end.end());

    std::vector<IqInstructionFile> files;
    files.push_back(std::move(ldb_axi));
    files.push_back(std::move(ldb));
    return files;
}

std::vector<IqInstructionFile> mul_scalar_store(const MulScalarOpParams& params,
                                                uint8_t smc_id_en,
                                                uint16_t lsu_loops,
                                                uint16_t lsu_inc,
                                                uint16_t axi_loops,
                                                uint16_t axi_inc,
                                                uint8_t axi_brst,
                                                uint32_t store_start_delay) {
    (void)lsu_inc;
    (void)axi_loops;
    (void)axi_inc;
    (void)axi_brst;

    IqInstructionFile stb;
    stb.para_iq_id = iq::IQ_ID_STB;
    stb.para_iq_name = iq::IQ_NAME_STB;
    stb.para_width_bits = 50 + 50;
    std::vector<uint8_t> stb_data;
    auto stb_idle = make_lsu_idle_instruction(50);
    append_repeat(stb_data, stb_idle, store_start_delay);
    stb.para_data = std::move(stb_data);

    IqInstructionFile stb_axi;
    stb_axi.para_iq_id = iq::IQ_ID_STBAXI;
    stb_axi.para_iq_name = "stb_axi";
    stb_axi.para_width_bits = 50 + 102;
    std::vector<uint8_t> stb_axi_data;
    auto stb_axi_idle = make_lsu_idle_instruction(102);
    append_repeat(stb_axi_data, stb_axi_idle, store_start_delay);
    stb_axi.para_data = std::move(stb_axi_data);

    const uint16_t output_ur_addr =
        encode_lsu_ur_addr(params.para_output_bank, params.para_output_addr);
    const uint32_t total_lines = lsu_total_lines_from_loops(lsu_loops);
    for (uint32_t off = 0; off < total_lines; off += 16u) {
        const uint32_t lines = std::min<uint32_t>(16u, total_lines - off);
        append_store_burst(stb, stb_axi, smc_id_en,
                           params.para_output_ddr_base_addr,
                           params.para_ddr_intlv_addr,
                           output_ur_addr,
                           off,
                           lines);
    }

    auto stb_end = make_lsu_end_instruction(50);
    stb.para_data.insert(stb.para_data.end(), stb_end.begin(), stb_end.end());
    auto stb_axi_end = make_lsu_end_instruction(102);
    stb_axi.para_data.insert(stb_axi.para_data.end(), stb_axi_end.begin(), stb_axi_end.end());

    std::vector<IqInstructionFile> files;
    files.push_back(std::move(stb));
    files.push_back(std::move(stb_axi));

    return files;
}

// v4.18 UR read IQ has a 20-bit code field:
//   {smc_id_en[4:0], raddr[7:0], rbank[2:0], re}
uint32_t encode_ur_read_code_v418(const iq::UrCru& cru, int port, uint8_t smc_id_en) {
    if (port < 0 || port >= 4) {
        throw std::out_of_range("encode_ur_read_code: port must be in [0, 3]");
    }
    const uint32_t bank = cru.para_raddr[port].bank & 0x7u;
    const uint32_t addr = cru.para_raddr[port].addr_word & 0xFFu;
    const uint32_t re = cru.para_re[port] ? 1u : 0u;
    return (static_cast<uint32_t>(smc_id_en & 0x1Fu) << 12) |
           (addr << 4) |
           (bank << 1) |
           re;
}

// v4.17 12-bit UR read code (no smc_id_en):
//   {raddr[7:0], rbank[2:0], re}
uint32_t encode_ur_read_code_v417(const iq::UrCru& cru, int port) {
    const uint32_t bank = cru.para_raddr[port].bank & 0x7u;
    const uint32_t addr = cru.para_raddr[port].addr_word & 0xFFu;
    const uint32_t re = cru.para_re[port] ? 1u : 0u;
    return (addr << 4) | (bank << 1) | re;
}

// v4.18 UR write IQ has a 33-bit code field:
//   {smc_id_en[4:0], wbe[15:0], waddr[7:0], wbank[2:0], we}
uint64_t encode_ur_write_code_v418(const iq::UrCru& cru, int port, uint8_t smc_id_en) {
    if (port < 0 || port >= 4) {
        throw std::out_of_range("encode_ur_write_code: port must be in [0, 3]");
    }
    const uint64_t bank = static_cast<uint64_t>(cru.para_waddr[port].bank & 0x7u);
    const uint64_t addr = static_cast<uint64_t>(cru.para_waddr[port].addr_word & 0xFFu);
    const uint64_t we = cru.para_we[port] ? 1u : 0u;
    const uint64_t wbe = static_cast<uint64_t>(cru.para_wbe[port] & 0xFFFFu);
    return (static_cast<uint64_t>(smc_id_en & 0x1Fu) << 28) |
           (wbe << 12) |
           (addr << 4) |
           (bank << 1) |
           we;
}

// v4.17 28-bit UR write code (no smc_id_en):
//   {wbe[15:0], waddr[7:0], wbank[2:0], we}
uint64_t encode_ur_write_code_v417(const iq::UrCru& cru, int port) {
    const uint64_t bank = static_cast<uint64_t>(cru.para_waddr[port].bank & 0x7u);
    const uint64_t addr = static_cast<uint64_t>(cru.para_waddr[port].addr_word & 0xFFu);
    const uint64_t we = cru.para_we[port] ? 1u : 0u;
    const uint64_t wbe = static_cast<uint64_t>(cru.para_wbe[port] & 0xFFFFu);
    return (wbe << 12) | (addr << 4) | (bank << 1) | we;
}
}

static void gen_ur_insts_impl(const MulScalarOpParams& params, MulScalarOpInstructions& result) {
    iq::UrCru ur_read_cru;
    gen_ur_read_inst(params, 0, ur_read_cru);
    result.para_ur_read_crus.push_back(ur_read_cru);

    iq::UrCru ur_write_cru;
    gen_ur_write_inst(params, 0, ur_write_cru);
    result.para_ur_write_crus.push_back(ur_write_cru);
}

static void gen_compute_impl(const MulScalarOpParams& params, MulScalarOpInstructions& result) {
    result.para_rtovr_routes.push_back({RTOVR_OUT_DVR_FPMUL_S0, RTOVR_IN_UR_RDATA_0});
    result.para_rtovr_routes.push_back({RTOVR_OUT_DVR_FPMUL_S1, RTOVR_IN_UR_RDATA_1});
    result.para_rtovr_routes.push_back({RTOVR_OUT_UVR_WDATA_0, RTOVR_IN_DR_FPMUL_D});

    iq::FpMulCru fpmul_cru;
    gen_fpmul_inst(params, fpmul_cru);
    result.para_fpmul_inst = iq::FpMulIq::pack_cru_fpmul(fpmul_cru);
}

std::vector<MulScalarChunkDesc> MulScalarOp::plan_full_shape_chunks(
    const MulScalarChunkPlan& plan) {
    if (plan.para_max_words_per_smc == 0) {
        throw std::invalid_argument("mul_scalar chunk plan requires max_words_per_smc > 0");
    }
    if (plan.para_num_smc == 0 || plan.para_num_smc > 8) {
        throw std::invalid_argument("mul_scalar chunk plan requires num_smc in [1, 8]");
    }
    if ((plan.para_total_bytes % kUrWordBytes) != 0) {
        throw std::invalid_argument("mul_scalar full-shape chunking requires 16-byte aligned tensor bytes");
    }

    const uint64_t total_lines = plan.para_total_bytes / kUrWordBytes;
    const uint64_t lines_per_chunk =
        static_cast<uint64_t>(plan.para_max_words_per_smc) * plan.para_num_smc;

    std::vector<MulScalarChunkDesc> chunks;
    for (uint64_t line_base = 0, idx = 0;
         line_base < total_lines;
         line_base += lines_per_chunk, ++idx) {
        MulScalarChunkDesc c;
        c.para_chunk_index = static_cast<uint32_t>(idx);
        c.para_global_line_base = line_base;

        uint16_t max_valid = 0;
        for (uint8_t smc = 0; smc < plan.para_num_smc; ++smc) {
            const uint64_t smc_line_base =
                line_base + static_cast<uint64_t>(smc) * plan.para_max_words_per_smc;
            uint16_t valid = 0;
            if (smc_line_base < total_lines) {
                const uint64_t remain = total_lines - smc_line_base;
                valid = static_cast<uint16_t>(
                    std::min<uint64_t>(remain, plan.para_max_words_per_smc));
            }
            c.para_valid_words_per_smc[smc] = valid;
            max_valid = std::max(max_valid, valid);
        }
        c.para_words_per_smc = max_valid;
        chunks.push_back(c);
    }
    return chunks;
}

MulScalarOpParams MulScalarOp::params_for_chunk(const MulScalarOpParams& base,
                                                const MulScalarChunkDesc& chunk) {
    MulScalarOpParams p = base;
    p.para_n = 1;
    p.para_c = 1;
    p.para_h = 1;
    p.para_w = static_cast<uint32_t>(chunk.para_words_per_smc) * p.get_elems_per_word();
    return p;
}

OpInstructionsBase* MulScalarOp::compile(const app::AppOperatorRequest& req) {
    if (!req.para_layout_mode.empty() &&
        req.para_layout_mode != "default" &&
        req.para_layout_mode != "ddr" &&
        req.para_layout_mode != "mul_scalar_ddr") {
        throw std::runtime_error("MulScalar DDR implementation supports only default/ddr layout");
    }

    MulScalarOpParams params;
    params.para_n = req.para_dimensions.para_n;
    params.para_c = req.para_dimensions.para_c;
    params.para_h = req.para_dimensions.para_h;
    params.para_w = req.para_dimensions.para_w;

    if (params.para_n == 0 || params.para_c == 0 || params.para_h == 0 || params.para_w == 0) {
        throw std::runtime_error("MulScalar: invalid dimensions");
    }

    auto input_it = req.para_inputs.find("input_tensor");
    if (input_it != req.para_inputs.end()) {
        params.para_dtype = (input_it->second.para_dtype == app::AppDataType::FLOAT16)
            ? MulScalarDataType::FP16
            : MulScalarDataType::FP32;
    } else if (!req.para_inputs.empty()) {
        const auto& first = req.para_inputs.begin()->second;
        params.para_dtype = (first.para_dtype == app::AppDataType::FLOAT16)
            ? MulScalarDataType::FP16
            : MulScalarDataType::FP32;
    }

    auto get_param = [&](const std::string& key, int def) -> int {
        auto it = req.para_parameters.find(key);
        if (it != req.para_parameters.end() && !it->second.para_data_i32.empty()) {
            return it->second.para_data_i32[0];
        }
        return def;
    };

    params.para_input_bank = static_cast<uint8_t>(get_param("input_bank", 0));
    params.para_input_addr = static_cast<uint8_t>(get_param("input_addr", 0));
    params.para_other_bank = static_cast<uint8_t>(get_param("other_bank", 1));
    params.para_other_addr = static_cast<uint8_t>(get_param("other_addr", 0));
    params.para_output_bank = static_cast<uint8_t>(get_param("output_bank", 2));
    params.para_output_addr = static_cast<uint8_t>(get_param("output_addr", 0));
    params.para_smc_id_en = static_cast<uint8_t>(get_param("smc_id_en", 0));
    params.para_input_ddr_base_addr =
        get_u64_param(req, "input_ddr_base_addr", get_u64_param(req, "ddr_input_base_addr", 0));
    params.para_other_ddr_base_addr =
        get_u64_param(req, "other_ddr_base_addr", get_u64_param(req, "scalar_ddr_base_addr", 0x100000ULL));
    params.para_output_ddr_base_addr =
        get_u64_param(req, "output_ddr_base_addr", get_u64_param(req, "ddr_output_base_addr", 0x200000ULL));
    params.para_ddr_intlv_addr =
        get_u64_param(req, "ddr_intlv_addr", kDefaultDdrIntlvAddr);
    params.para_compute_start_delay_insts = static_cast<uint32_t>(
        get_param("compute_start_delay_insts", 0));
    params.para_store_start_delay_insts = static_cast<uint32_t>(
        get_param("store_start_delay_insts", 0));
    params.para_identity_scalar = (get_other_scalar(req) == 1.0f);

    if (params.para_smc_id_en > 31) {
        throw std::runtime_error("MulScalar: smc_id_en must be in [0, 31]");
    }

    if (params.para_input_bank == params.para_other_bank ||
        params.para_input_bank == params.para_output_bank ||
        params.para_other_bank == params.para_output_bank) {
        throw std::runtime_error("MulScalar requires distinct input/other/output banks");
    }

    auto* insts = new MulScalarOpInstructions();
    *insts = compile(params);
    return insts;
}

MulScalarOpInstructions MulScalarOp::compile(const MulScalarOpParams& params) {
    MulScalarOpInstructions result;
    result.para_dtype = params.para_dtype;
    result.para_smc_id_en = params.para_smc_id_en;
    result.para_params = params;

    const uint32_t total_elems = params.para_n * params.para_c * params.para_h * params.para_w;
    const uint32_t elems_per_word = params.get_elems_per_word();
    const uint32_t total_steps = (total_elems + elems_per_word - 1) / elems_per_word;

    if (total_steps == 0) {
        throw std::runtime_error("MulScalar: total_steps is zero");
    }

    result.para_total_elements = total_elems;
    result.para_total_steps = total_steps;

    gen_ur_insts_impl(params, result);
    if (!params.para_identity_scalar) {
        gen_compute_impl(params, result);
    }

    constexpr uint32_t kFpmulLatency = 4;
    constexpr uint32_t kBatch = 4;
    result.para_total_cycles = params.para_identity_scalar
        ? total_steps
        : kFpmulLatency + (total_steps - 1) * kBatch;

    std::cout << "[MulScalar] DDR load/compute/store config:\n"
              << "  Input:  bank=" << static_cast<int>(params.para_input_bank)
              << ", addr=" << static_cast<int>(params.para_input_addr) << "\n"
              << "  Other:  bank=" << static_cast<int>(params.para_other_bank)
              << ", addr=" << static_cast<int>(params.para_other_addr)
              << " (scalar, fixed-read)\n"
              << "  Output: bank=" << static_cast<int>(params.para_output_bank)
              << ", addr=" << static_cast<int>(params.para_output_addr) << "\n"
              << "  SMC:    smc_id_en=" << static_cast<int>(params.para_smc_id_en) << "\n"
              << "  Mode:   " << (params.para_identity_scalar ? "identity load/store (scalar=1)" : "fpmul") << "\n"
              << "  DDR:    input=0x" << std::hex << params.para_input_ddr_base_addr
              << ", scalar=0x" << params.para_other_ddr_base_addr
              << ", output=0x" << params.para_output_ddr_base_addr
              << ", intlv=0x" << params.para_ddr_intlv_addr << std::dec << "\n";

    return result;
}

std::vector<IqInstructionFile> MulScalarOpInstructions::get_iq_files() const {
    std::vector<IqInstructionFile> files;

    constexpr uint32_t BITS_EMBED = 3;
    constexpr uint32_t BITS_EMBED_END = 6;
    constexpr uint32_t BITS_LOOPS = 10;
    constexpr uint32_t BITS_COND = 7;
    constexpr uint32_t BITS_DLY = 3;
    constexpr uint32_t BITS_INC = 4;
    constexpr uint32_t BITS_INC_EMBED = 6;
    constexpr uint32_t MASK_EMBED = (1u << BITS_EMBED) - 1;
    constexpr uint32_t MASK_EMBED_END = (1u << BITS_EMBED_END) - 1;
    constexpr uint32_t MASK_LOOPS = (1u << BITS_LOOPS) - 1;
    constexpr uint32_t MASK_COND = (1u << BITS_COND) - 1;
    constexpr uint32_t MASK_DLY = (1u << BITS_DLY) - 1;
    constexpr uint32_t MASK_INC = (1u << BITS_INC) - 1;
    constexpr uint32_t MASK_INC_EMBED = (1u << BITS_INC_EMBED) - 1;

    auto serialize_header_bits = [&](uint8_t vld, uint8_t jump, uint8_t embed, uint16_t loops,
                                     uint8_t cond, uint8_t dly, uint8_t inc,
                                     uint8_t embed_end_bits, uint8_t inc_embed_bits) -> uint64_t {
        uint64_t bits = 0;
        uint32_t pos = 0;
        const uint8_t embed_end = (embed == 0) ? (embed_end_bits & MASK_EMBED_END) : 0u;
        const uint8_t inc_embed = (embed == 0) ? (inc_embed_bits & MASK_INC_EMBED) : 0u;

        bits |= static_cast<uint64_t>(inc_embed & MASK_INC_EMBED) << pos; pos += BITS_INC_EMBED;
        bits |= static_cast<uint64_t>(inc & MASK_INC) << pos; pos += BITS_INC;
        bits |= static_cast<uint64_t>(dly & MASK_DLY) << pos; pos += BITS_DLY;
        bits |= static_cast<uint64_t>(cond & MASK_COND) << pos; pos += BITS_COND;
        bits |= static_cast<uint64_t>(loops & MASK_LOOPS) << pos; pos += BITS_LOOPS;
        bits |= static_cast<uint64_t>(embed_end & MASK_EMBED_END) << pos; pos += BITS_EMBED_END;
        bits |= static_cast<uint64_t>(embed & MASK_EMBED) << pos; pos += BITS_EMBED;
        bits |= static_cast<uint64_t>(jump & 1u) << pos; pos += 1;
        bits |= static_cast<uint64_t>(vld & 1u) << pos;
        return bits;
    };

    auto serialize_header = [&](uint8_t vld, uint8_t jump, uint8_t embed, uint16_t loops,
                                uint8_t cond, uint8_t dly, uint8_t inc,
                                uint8_t embed_end_bits, uint8_t inc_embed_bits) -> std::vector<uint8_t> {
        const uint64_t bits = serialize_header_bits(vld, jump, embed, loops, cond, dly, inc,
                                                    embed_end_bits, inc_embed_bits);
        std::vector<uint8_t> bytes;
        for (int i = 5; i >= 0; --i) {
            bytes.push_back(static_cast<uint8_t>((bits >> (i * 8)) & 0xFF));
        }
        return bytes;
    };

    auto serialize_code = [](uint64_t code, uint16_t code_bits) -> std::vector<uint8_t> {
        std::vector<uint8_t> bytes;
        const uint16_t num_bytes = (code_bits + 7) / 8;
        for (int i = num_bytes - 1; i >= 0; --i) {
            bytes.push_back(static_cast<uint8_t>((code >> (i * 8)) & 0xFF));
        }
        return bytes;
    };

    auto make_instruction = [&](uint8_t vld, uint8_t jump, uint8_t embed, uint16_t loops,
                                uint8_t cond, uint8_t dly, int8_t inc,
                                uint8_t embed_end_bits, uint8_t inc_embed_bits,
                                const std::vector<uint8_t>& code_bytes,
                                int code_bits = -1) -> std::vector<uint8_t> {
        if (code_bits > 0 && code_bits <= 23) {
            const uint64_t h = serialize_header_bits(vld, jump, embed, loops, cond, dly,
                                                     static_cast<uint8_t>(inc & 0xF),
                                                     embed_end_bits, inc_embed_bits);
            uint64_t c = 0;
            for (uint8_t b : code_bytes) {
                c = (c << 8) | b;
            }
            if (code_bits < 64) {
                c &= ((1ULL << code_bits) - 1ULL);
            }
            const uint64_t total = (h << code_bits) | c;
            const int total_width = 41 + code_bits;
            const int num_bytes = (total_width + 7) / 8;
            std::vector<uint8_t> ret;
            for (int i = num_bytes - 1; i >= 0; --i) {
                ret.push_back(static_cast<uint8_t>((total >> (i * 8)) & 0xFF));
            }
            return ret;
        }

        auto header = serialize_header(vld, jump, embed, loops, cond, dly,
                                       static_cast<uint8_t>(inc & 0xF),
                                       embed_end_bits, inc_embed_bits);
        header.insert(header.end(), code_bytes.begin(), code_bytes.end());
        return header;
    };

    // Generic UR wdata serializer: handles both v4.17 (28-bit code, 69-bit total)
    // and v4.18 (33-bit code, 74-bit total).
    auto serialize_ur_wdata_inst = [&](uint8_t vld, uint8_t jump, uint8_t embed, uint16_t loops,
                                       uint8_t cond, uint8_t dly, uint8_t inc,
                                       uint8_t embed_end_bits, uint8_t inc_embed_bits,
                                       uint64_t code, uint16_t code_bits) -> std::vector<uint8_t> {
        const uint64_t header = serialize_header_bits(vld, jump, embed, loops, cond, dly, inc,
                                                      embed_end_bits, inc_embed_bits);
        const uint64_t code_mask = (code_bits >= 64) ? ~0ULL : ((1ULL << code_bits) - 1ULL);
        const unsigned __int128 inst =
            (static_cast<unsigned __int128>(header) << code_bits) |
            static_cast<unsigned __int128>(code & code_mask);
        const int total_width = 41 + code_bits;
        const int num_bytes = (total_width + 7) / 8;
        std::vector<uint8_t> bytes;
        for (int i = num_bytes - 1; i >= 0; --i) {
            bytes.push_back(static_cast<uint8_t>((inst >> (i * 8)) & 0xFF));
        }
        return bytes;
    };

    auto append_nop_wait = [&](IqInstructionFile& file, uint16_t code_bits, uint32_t cycles) {
        const auto zero_code = serialize_code(0, code_bits);
        while (cycles > 0) {
            const uint32_t chunk = std::min<uint32_t>(cycles, 1024);
            const uint16_t loops = static_cast<uint16_t>(chunk - 1);
            std::vector<uint8_t> nop;
            if (code_bits > 23) {
                nop = serialize_ur_wdata_inst(0, 0, 7, loops, 0, 0, 0, 0, 0,
                                              0, code_bits);
            } else {
                nop = make_instruction(0, 0, 7, loops, 0, 0, 0, 0, 0,
                                       zero_code, code_bits);
            }
            file.para_data.insert(file.para_data.end(), nop.begin(), nop.end());
            cycles -= chunk;
        }
    };

    // Compensation for the CRU cascade delay: downstream SMCs receive
    // read/route/compute controls later, so the compute-side controls must
    // stay valid long enough for the last active SMC to consume all inputs.
    //
    // Do not extend the UR write window with the same compensation.  Writeback
    // must cover only real operator output rows; otherwise short streams write
    // stale pipeline data into the UR tail region after the valid output.
    const uint16_t cru_delay_comp = (para_smc_id_en > 0) ? static_cast<uint16_t>(para_smc_id_en) : 0u;
    const uint16_t loops_write = (para_total_steps >= 1)
        ? static_cast<uint16_t>(para_total_steps - 1)
        : 0u;
    const uint16_t loops_compute = (para_total_steps >= 1)
        ? static_cast<uint16_t>(para_total_steps - 1) + cru_delay_comp
        : 0u;
    const uint8_t inc_r0 = 1u;
    const uint8_t inc_r1 = 0u;
    const uint8_t inc_w0 = 1u;

    // Always use v4.18 new encoding with smc_id_en bits (even when smc_id_en==0,
    // the upper 5 bits are simply zero).  v4.17_fresh RTL expects this format.
    const uint16_t ur_read_code_bits  = 17u;  // low 17 bits drive current RTL UR read
    const uint16_t ur_write_code_bits = 33u;  // 28 + 5 smc_id_en
    const uint32_t compute_start_delay =
        std::min<uint32_t>(para_params.para_compute_start_delay_insts, 256);
    const uint32_t store_start_delay =
        std::min<uint32_t>(para_params.para_store_start_delay_insts, 512);
    const uint16_t lsu_loops = loops_write;
    const uint16_t lsu_inc = (para_total_steps > 1) ? kUrWordBytes : 0u;
    const uint32_t axi_beats = std::min<uint32_t>(para_total_steps, 16);
    const uint8_t axi_brst = static_cast<uint8_t>((axi_beats > 0) ? (axi_beats - 1) : 0);
    const uint16_t axi_loops = static_cast<uint16_t>((para_total_steps + 15) / 16 - 1);
    const uint16_t axi_inc = (axi_loops > 0) ? 0x100u : 0u;

    auto load_files = para_params.para_identity_scalar
        ? mul_scalar_identity_load(para_params, para_smc_id_en, lsu_loops,
                                   lsu_inc, axi_loops, axi_inc, axi_brst)
        : mul_scalar_load(para_params, para_smc_id_en, lsu_loops,
                          lsu_inc, axi_loops, axi_inc, axi_brst);
    files.insert(files.end(),
                 std::make_move_iterator(load_files.begin()),
                 std::make_move_iterator(load_files.end()));

    if (para_params.para_identity_scalar) {
        auto store_files = mul_scalar_store(para_params, para_smc_id_en, lsu_loops,
                                            lsu_inc, axi_loops, axi_inc, axi_brst,
                                            store_start_delay);
        files.insert(files.end(),
                     std::make_move_iterator(store_files.begin()),
                     std::make_move_iterator(store_files.end()));
        return files;
    }

    {
        IqInstructionFile file;
        file.para_iq_id = iq::IQ_ID_UR_RDATA_0;
        file.para_iq_name = iq::IQ_NAME_UR_RDATA_0;
        file.para_width_bits = 41 + ur_read_code_bits;

        const uint32_t code = encode_ur_read_code_v418(para_ur_read_crus[0], 0, para_smc_id_en);
        const auto code_bytes = serialize_code(code, ur_read_code_bits);
        const auto zero_code = serialize_code(0, ur_read_code_bits);

        // DDR load latency is a long wait; use NOP loops, then keep the
        // normal short DLY alignment between compute-side IQs.
        append_nop_wait(file, ur_read_code_bits, compute_start_delay);
        const uint8_t dly = 0;
        auto inst = make_instruction(1, 0, 0, loops_compute, 0, dly, inc_r0, 0, 0, code_bytes, ur_read_code_bits);
        file.para_data.insert(file.para_data.end(), inst.begin(), inst.end());
        files.push_back(file);
    }

    {
        IqInstructionFile file;
        file.para_iq_id = iq::IQ_ID_UR_RDATA_1;
        file.para_iq_name = iq::IQ_NAME_UR_RDATA_1;
        file.para_width_bits = 41 + ur_read_code_bits;

        const uint32_t code = encode_ur_read_code_v418(para_ur_read_crus[0], 1, para_smc_id_en);
        const auto code_bytes = serialize_code(code, ur_read_code_bits);
        const auto zero_code = serialize_code(0, ur_read_code_bits);

        // True-scalar stream: same scalar word is reused every step.
        append_nop_wait(file, ur_read_code_bits, compute_start_delay);
        const uint8_t dly = 0;
        auto inst = make_instruction(1, 0, 0, loops_compute, 0, dly, inc_r1, 0, 0, code_bytes, ur_read_code_bits);
        file.para_data.insert(file.para_data.end(), inst.begin(), inst.end());
        files.push_back(file);
    }

    for (const auto& route : para_rtovr_routes) {
        const uint8_t target_port = route.first;
        const uint8_t src_id = route.second;
        const bool is_read_route =
            (target_port == RTOVR_OUT_DVR_FPMUL_S0 || target_port == RTOVR_OUT_DVR_FPMUL_S1);

        IqInstructionFile file;
        file.para_iq_id = is_read_route ? iq::IQ_ID_RTOVR_TO_FPMUL : iq::IQ_ID_RTOVR_TO_UR;
        file.para_iq_name = "rtovr-" + std::to_string(target_port);
        file.para_width_bits = 41 + 7;

        const uint8_t route_code = static_cast<uint8_t>((src_id << 1) | 1u);
        const auto code_bytes = serialize_code(route_code, 7);
        const auto zero_code = serialize_code(0u, 7);

        // Align with NEG-style stage timing:
        //   route-to-compute at t=1, route-to-writeback at t=6.
        append_nop_wait(file, 7, compute_start_delay);
        const uint8_t dly = static_cast<uint8_t>(is_read_route ? 1u : 6u);
        auto inst = make_instruction(1, 0, 0, loops_compute, 0, dly, 0, 0, 0, code_bytes, 7);
        file.para_data.insert(file.para_data.end(), inst.begin(), inst.end());
        files.push_back(file);
    }

    {
        IqInstructionFile file;
        file.para_iq_id = iq::IQ_ID_FPMUL;
        file.para_iq_name = iq::IQ_NAME_FPMUL;
        // FPMUL code = {smc_id_en[4:0], original_code[2:0]} = 8 bits (v4.18 new encoding)
        constexpr uint16_t kFpmulCodeBits = 8u;
        file.para_width_bits = 41 + kFpmulCodeBits;

        const uint32_t fpmul_code_final =
            static_cast<uint32_t>(((para_smc_id_en & 0x1Fu) << 3) | (para_fpmul_inst & 0x7u));
        const auto code_bytes = serialize_code(fpmul_code_final, kFpmulCodeBits);
        const auto zero_code = serialize_code(0u, kFpmulCodeBits);

        // FPMUL fires at t=2 (after UR read + RtoVR setup).
        append_nop_wait(file, kFpmulCodeBits, compute_start_delay);
        const uint8_t dly = 2;
        auto inst = make_instruction(1, 0, 0, loops_compute, 0, dly, 0, 0, 0, code_bytes, kFpmulCodeBits);
        file.para_data.insert(file.para_data.end(), inst.begin(), inst.end());
        files.push_back(file);
    }

    {
        IqInstructionFile file;
        file.para_iq_id = iq::IQ_ID_UR_WDATA_0;
        file.para_iq_name = iq::IQ_NAME_UR_WDATA_0;
        file.para_width_bits = 41 + ur_write_code_bits;

        const uint64_t code = encode_ur_write_code_v418(para_ur_write_crus[0], 0, para_smc_id_en);

        // Writeback at t=7 (after FPMUL latency and route-to-UVR ready).
        append_nop_wait(file, ur_write_code_bits, compute_start_delay);
        const uint8_t dly = 7;

        auto inst = serialize_ur_wdata_inst(1, 0, 0, loops_write, 0, dly, inc_w0, 0, 0, code, ur_write_code_bits);
        file.para_data.insert(file.para_data.end(), inst.begin(), inst.end());
        files.push_back(file);
    }

    auto store_files = mul_scalar_store(para_params, para_smc_id_en, lsu_loops,
                                        lsu_inc, axi_loops, axi_inc, axi_brst,
                                        store_start_delay);
    files.insert(files.end(),
                 std::make_move_iterator(store_files.begin()),
                 std::make_move_iterator(store_files.end()));

    return files;
}

std::vector<uint8_t> MulScalarOpInstructions::serialize() const {
    std::vector<uint8_t> result;
    auto push_u32 = [&](uint32_t v) {
        result.push_back(v & 0xFF);
        result.push_back((v >> 8) & 0xFF);
        result.push_back((v >> 16) & 0xFF);
        result.push_back((v >> 24) & 0xFF);
    };

    push_u32(para_total_steps);
    push_u32(para_total_elements);
    push_u32(para_total_cycles);
    result.push_back(para_fpmul_inst);
    result.push_back(para_smc_id_en);

    for (const auto& r : para_rtovr_routes) {
        result.push_back(r.first);
        result.push_back(r.second);
    }

    for (const auto& cru : para_ur_read_crus) {
        uint32_t ur_words[7];
        iq::UrIq::pack_cru_ur_words(cru, ur_words);
        for (int i = 0; i < 7; ++i) {
            push_u32(ur_words[i]);
        }
    }

    for (const auto& cru : para_ur_write_crus) {
        uint32_t ur_words[7];
        iq::UrIq::pack_cru_ur_words(cru, ur_words);
        for (int i = 0; i < 7; ++i) {
            push_u32(ur_words[i]);
        }
    }

    return result;
}

std::string MulScalarOp::get_name() const {
    return "MulScalar";
}

bool MulScalarOp::calculate_golden(const app::AppOperatorRequest& req,
                                   std::vector<uint8_t>& golden_bin) {
    golden_bin.clear();

    auto it = req.para_inputs.find("input_tensor");
    if (it == req.para_inputs.end()) {
        it = req.para_inputs.find("input");
    }
    if (it == req.para_inputs.end()) {
        return false;
    }

    const auto& input = it->second;
    const app::AppDataType dtype = input.para_dtype;
    const float scalar = get_other_scalar(req);
    const bool ddr_mode = is_ddr_layout(req);

    if (dtype == app::AppDataType::FLOAT16) {
        const uint16_t s16 = app::float32_to_float16(scalar);
        const float s_truncated = app::float16_to_float32(s16);
        for (float x : input.para_data_f32) {
            const uint16_t x16 = app::float32_to_float16(x);
            const float x_truncated = app::float16_to_float32(x16);
            const float y = x_truncated * s_truncated;
            const uint16_t out = app::float32_to_float16(y);
            if (ddr_mode) {
                append_u16_be(golden_bin, out);
            } else {
                append_u16_le(golden_bin, out);
            }
        }
    } else {
        for (float x : input.para_data_f32) {
            const float y = x * scalar;
            uint32_t bits = 0;
            std::memcpy(&bits, &y, sizeof(float));
            if (ddr_mode) {
                append_u32_be(golden_bin, bits);
            } else {
                append_u32_le(golden_bin, bits);
            }
        }
    }

    return true;
}

void MulScalarOp::fill_random_request(app::AppOperatorRequest& req,
                                      std::mt19937& rng,
                                      std::uniform_real_distribution<float>& data_dist,
                                      const std::string& dtype_str) {
    req.para_operator = "MulScalar";
    req.para_layout_mode = "mul_scalar_ddr";

    const app::AppDataType target_type = (dtype_str == "fp16")
        ? app::AppDataType::FLOAT16
        : app::AppDataType::FLOAT32;

    const uint32_t total = req.para_dimensions.para_n * req.para_dimensions.para_c *
                           req.para_dimensions.para_h * req.para_dimensions.para_w;

    app::AppTensorDesc input_desc;
    input_desc.para_name = "input_tensor";
    input_desc.para_dtype = target_type;
    input_desc.para_shape = {
        req.para_dimensions.para_n,
        req.para_dimensions.para_c,
        req.para_dimensions.para_h,
        req.para_dimensions.para_w
    };
    input_desc.para_data_f32.reserve(total);
    for (uint32_t i = 0; i < total; ++i) {
        input_desc.para_data_f32.push_back(data_dist(rng));
    }
    req.para_inputs["input_tensor"] = input_desc;

    app::AppTensorDesc scalar_desc;
    scalar_desc.para_name = "other_scalar";
    scalar_desc.para_dtype = app::AppDataType::FLOAT32;
    scalar_desc.para_shape = {1};
    scalar_desc.para_data_f32.push_back(data_dist(rng));
    req.para_parameters["other_scalar"] = scalar_desc;

    auto set_i32_param = [&](const std::string& name, int v) {
        app::AppTensorDesc d;
        d.para_name = name;
        d.para_dtype = app::AppDataType::INT32;
        d.para_shape = {1};
        d.para_data_i32.push_back(v);
        req.para_parameters[name] = d;
    };

    set_i32_param("input_bank", 0);
    set_i32_param("input_addr", 0);
    set_i32_param("other_bank", 1);
    set_i32_param("other_addr", 0);
    set_i32_param("output_bank", 2);
    set_i32_param("output_addr", 0);
    set_i32_param("smc_id_en", 0);
    set_i32_param("input_ddr_base_addr", 0);
    set_i32_param("other_ddr_base_addr", 0x100000);
    set_i32_param("output_ddr_base_addr", 0x200000);
    set_i32_param("ddr_intlv_addr", 0x10000000);
    set_i32_param("compute_start_delay_insts", 0);
    set_i32_param("store_start_delay_insts", 0);
}

bool MulScalarOp::generate_data(const app::AppOperatorRequest& req,
                                std::vector<uint8_t>& input_bin,
                                std::vector<uint8_t>& other_bin,
                                std::vector<uint8_t>& alpha_bin) {
    input_bin.clear();
    other_bin.clear();
    alpha_bin.clear();

    auto it = req.para_inputs.find("input_tensor");
    if (it == req.para_inputs.end()) {
        it = req.para_inputs.find("input");
    }
    if (it == req.para_inputs.end()) {
        return false;
    }

    const auto& input = it->second;
    const app::AppDataType dtype = input.para_dtype;
    const float scalar = get_other_scalar(req);
    const bool ddr_mode = is_ddr_layout(req);

    if (dtype == app::AppDataType::FLOAT16) {
        for (float v : input.para_data_f32) {
            const uint16_t bits = app::float32_to_float16(v);
            if (ddr_mode) {
                append_u16_be(input_bin, bits);
            } else {
                append_u16_le(input_bin, bits);
            }
        }

        // True-scalar semantics on vector ALU:
        // keep r1 inc=0 and materialize one 128-bit scalar word with all lanes identical.
        const uint16_t s16 = app::float32_to_float16(scalar);
        constexpr int kFp16LanesPerWord = 8;
        for (int lane = 0; lane < kFp16LanesPerWord; ++lane) {
            if (ddr_mode) {
                append_u16_be(other_bin, s16);
            } else {
                append_u16_le(other_bin, s16);
            }
        }
    } else {
        for (float v : input.para_data_f32) {
            uint32_t bits = 0;
            std::memcpy(&bits, &v, sizeof(float));
            if (ddr_mode) {
                append_u32_be(input_bin, bits);
            } else {
                append_u32_le(input_bin, bits);
            }
        }

        uint32_t s32 = 0;
        std::memcpy(&s32, &scalar, sizeof(float));
        constexpr int kFp32LanesPerWord = 4;
        for (int lane = 0; lane < kFp32LanesPerWord; ++lane) {
            if (ddr_mode) {
                append_u32_be(other_bin, s32);
            } else {
                append_u32_le(other_bin, s32);
            }
        }
    }

    return true;
}

void print_mul_scalar_instructions(const MulScalarOpInstructions& insts) {
    std::cout << "=== MulScalarOpInstructions ===\n"
              << "  total_steps:    " << insts.para_total_steps << "\n"
              << "  total_elements: " << insts.para_total_elements << "\n"
              << "  total_cycles:   " << insts.para_total_cycles << "\n"
              << "  ur_read_crus:   " << insts.para_ur_read_crus.size() << "\n"
              << "  ur_write_crus:  " << insts.para_ur_write_crus.size() << "\n"
              << "  rtovr_routes:   " << insts.para_rtovr_routes.size() << "\n"
              << "  fpmul_inst:     0x" << std::hex << static_cast<int>(insts.para_fpmul_inst)
              << std::dec << "\n"
              << "  smc_id_en:      " << static_cast<int>(insts.para_smc_id_en) << "\n";
}

REGISTER_OP(MulScalarOp);

} // namespace op
} // namespace aiacc
