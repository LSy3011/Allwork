#include "select_int_op.h"

#include <algorithm>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <numeric>
#include <sstream>
#include <set>
#include <stdexcept>

#include "../../app/fp16_utils.h"
#include "../../iq/iq_id_defs.h"

namespace aiacc {
namespace op {
namespace {

constexpr size_t kUrWordBytes = 16;
constexpr uint32_t kDefaultLoopChunkWords = 256;
constexpr uint32_t kMaxLoopWords = 256;
constexpr uint32_t kMaxAxiBurstWords = 16;
constexpr uint32_t kMaxUrAddr = 255;
constexpr uint32_t kV419IqIdStb = 24;
constexpr uint32_t kV419IqIdStbAxi = 25;
constexpr uint32_t kV419IqIdLdb = 26;
constexpr uint32_t kV419IqIdLdbAxi = 27;
constexpr uint64_t kDefaultDdrIntlvAddr = 0x10000000ULL;

// RtoVR input port numbers (from ISA Table 5-64)
constexpr uint8_t kRtovrInputUrRdata0   = 30;  // UR_RDATA_0
constexpr uint8_t kRtovrInputUrRdata1   = 31;  // UR_RDATA_1
constexpr uint8_t kRtovrInputDrLogicD   = 10;  // DR_LOGIC_D

// RtoVR output port numbers (from ISA Table 5-64)
constexpr uint8_t kRtovrOutputDvrLogicS0 = 11;  // DVR_LOGIC_S0
constexpr uint8_t kRtovrOutputDvrLogicS1 = 12;  // DVR_LOGIC_S1
constexpr uint8_t kRtovrOutputUvrWdata0  = 35;  // UVR_WDATA_0

// RtoVR 7-bit port code: {select[6], vld[1]} where vld is at LSB (bit 0)
// From ISA Table 5-66: mv_out*_vld at bit 0, mv_out* at bits [6:1]
constexpr uint8_t make_rtovr_port_code(uint8_t input_port) {
    return static_cast<uint8_t>(((input_port & 0x3Fu) << 1) | 0x1u);  // vld=1, select=input_port
}

// LOGIC op_type encodings (from ISA Table 5-41)
constexpr uint8_t kLogicOpCopy           = 0x4u;
constexpr uint8_t kLogicOpLogicLeftShift  = 0x8u;
constexpr uint8_t kLogicOpLogicRightShift = 0xBu;

int get_i32_param(const app::AppOperatorRequest& req,
                  const std::string& name,
                  int default_value) {
    auto it = req.para_parameters.find(name);
    if (it == req.para_parameters.end() || it->second.para_data_i32.empty()) {
        return default_value;
    }
    return it->second.para_data_i32[0];
}

uint64_t get_u64_param(const app::AppOperatorRequest& req,
                       const std::string& name,
                       uint64_t default_value) {
    auto it = req.para_parameters.find(name);
    if (it == req.para_parameters.end() || it->second.para_data_i32.empty()) {
        return default_value;
    }
    return static_cast<uint64_t>(static_cast<uint32_t>(it->second.para_data_i32[0]));
}

bool has_i32_param(const app::AppOperatorRequest& req, const std::string& name) {
    auto it = req.para_parameters.find(name);
    return it != req.para_parameters.end() && !it->second.para_data_i32.empty();
}

std::vector<int64_t> get_i32_array_param(const app::AppOperatorRequest& req,
                                         const std::string& name) {
    std::vector<int64_t> result;
    auto it = req.para_parameters.find(name);
    if (it == req.para_parameters.end()) return result;
    result.reserve(it->second.para_data_i32.size());
    for (int32_t v : it->second.para_data_i32) {
        result.push_back(static_cast<int64_t>(v));
    }
    return result;
}

const app::AppTensorDesc* find_input_tensor(const app::AppOperatorRequest& req) {
    auto it = req.para_inputs.find("input_tensor");
    if (it != req.para_inputs.end()) return &it->second;
    it = req.para_inputs.find("input");
    if (it != req.para_inputs.end()) return &it->second;
    return nullptr;
}

uint32_t total_elements_from_shape(const app::AppTensorDesc& tensor,
                                   const app::AppOperatorRequest& req) {
    if (!tensor.para_shape.empty()) {
        uint32_t total = 1;
        for (uint32_t dim : tensor.para_shape) total *= dim;
        return total;
    }
    if (!tensor.para_data_f32.empty()) return static_cast<uint32_t>(tensor.para_data_f32.size());
    if (!tensor.para_data_i32.empty()) return static_cast<uint32_t>(tensor.para_data_i32.size());
    return req.para_dimensions.para_n * req.para_dimensions.para_c *
           req.para_dimensions.para_h * req.para_dimensions.para_w;
}

size_t bytes_per_element(app::AppDataType dtype) {
    switch (dtype) {
        case app::AppDataType::FLOAT16: return 2;
        case app::AppDataType::FLOAT32: return 4;
        case app::AppDataType::INT32: return 4;
        default: return 4;
    }
}

uint16_t words_for_input(const app::AppTensorDesc& tensor,
                         const app::AppOperatorRequest& req) {
    const uint32_t elems = total_elements_from_shape(tensor, req);
    const size_t total_bytes = static_cast<size_t>(elems) * bytes_per_element(tensor.para_dtype);
    return static_cast<uint16_t>((total_bytes + kUrWordBytes - 1) / kUrWordBytes);
}

uint64_t product_dims(const std::vector<int64_t>& dims, size_t begin, size_t end) {
    uint64_t prod = 1;
    for (size_t i = begin; i < end; ++i) {
        prod *= static_cast<uint64_t>(dims[i]);
    }
    return prod;
}

std::vector<int64_t> tensor_shape_i64(const app::AppTensorDesc& tensor,
                                      const app::AppOperatorRequest& req) {
    std::vector<int64_t> shape;
    if (!tensor.para_shape.empty()) {
        shape.reserve(tensor.para_shape.size());
        for (uint32_t d : tensor.para_shape) shape.push_back(static_cast<int64_t>(d));
        return shape;
    }
    shape = {static_cast<int64_t>(req.para_dimensions.para_n),
             static_cast<int64_t>(req.para_dimensions.para_c),
             static_cast<int64_t>(req.para_dimensions.para_h),
             static_cast<int64_t>(req.para_dimensions.para_w)};
    return shape;
}

std::vector<int64_t> contiguous_stride(const std::vector<int64_t>& shape) {
    std::vector<int64_t> stride(shape.size(), 1);
    for (int i = static_cast<int>(shape.size()) - 2; i >= 0; --i) {
        stride[static_cast<size_t>(i)] =
            stride[static_cast<size_t>(i + 1)] * shape[static_cast<size_t>(i + 1)];
    }
    return stride;
}

bool inner_dims_are_contiguous(const std::vector<int64_t>& shape,
                               const std::vector<int64_t>& stride,
                               size_t dim) {
    if (shape.size() != stride.size()) return false;
    int64_t expected = 1;
    for (int i = static_cast<int>(shape.size()) - 1; i > static_cast<int>(dim); --i) {
        if (stride[static_cast<size_t>(i)] != expected) return false;
        expected *= shape[static_cast<size_t>(i)];
    }
    return true;
}

void mark_unsupported(SelectIntOpParams& params, const std::string& msg) {
    params.para_is_supported = false;
    params.para_error_msg = msg;
    params.para_segments.clear();
    params.para_select_words = 0;
    params.para_output_bytes = 0;
    params.para_has_logic_segments = false;
}

bool map_linear_word_to_bank_addr(uint8_t base_bank,
                                  uint16_t base_addr,
                                  uint32_t bank_stride_words,
                                  uint64_t linear_word,
                                  uint8_t& bank,
                                  uint16_t& addr) {
    if (bank_stride_words == 0) {
        bank = base_bank;
        const uint64_t mapped_addr = static_cast<uint64_t>(base_addr) + linear_word;
        if (mapped_addr > kMaxUrAddr) return false;
        addr = static_cast<uint16_t>(mapped_addr);
        return true;
    }

    const uint64_t bank_offset = linear_word / bank_stride_words;
    const uint64_t addr_offset = linear_word % bank_stride_words;
    const uint64_t mapped_bank = static_cast<uint64_t>(base_bank) + bank_offset;
    const uint64_t mapped_addr = static_cast<uint64_t>(base_addr) + addr_offset;
    if (mapped_bank > 7 || mapped_addr > kMaxUrAddr) return false;
    bank = static_cast<uint8_t>(mapped_bank);
    addr = static_cast<uint16_t>(mapped_addr);
    return true;
}

bool segment_range_fits_bank(uint16_t addr, uint32_t words, uint32_t bank_stride_words) {
    if (words == 0) return false;
    const uint32_t limit = (bank_stride_words == 0)
                               ? (kMaxUrAddr + 1)
                               : std::min<uint32_t>(bank_stride_words, kMaxUrAddr + 1);
    return static_cast<uint32_t>(addr) + words <= limit;
}

// v4.18 UR read IQ has a 20-bit code field.  The current RTL consumes the low
// 17 bits as {smc_id_en[4:0], raddr[7:0], rbank[2:0], re}; the top 3 bits are
// unused padding kept for the SMI IQ layout.
uint32_t encode_ur_read_code(uint8_t bank, uint16_t addr, uint8_t smc_id_en) {
    return (static_cast<uint32_t>(smc_id_en & 0x1Fu) << 12) |
           (static_cast<uint32_t>(addr & 0xFFu) << 4) |
           (static_cast<uint32_t>(bank & 0x7u) << 1) |
           0x1u;
}

uint64_t serialize_header_bits(uint8_t vld, uint8_t jump, uint8_t embed, uint16_t loops,
                               uint8_t cond, uint8_t dly, uint8_t inc,
                               uint8_t embed_end_bits, uint8_t inc_embed_bits) {
    constexpr uint32_t bits_embed = 3;
    constexpr uint32_t bits_embed_end = 6;
    constexpr uint32_t bits_loops = 10;
    constexpr uint32_t bits_cond = 7;
    constexpr uint32_t bits_dly = 3;
    constexpr uint32_t bits_inc = 4;
    constexpr uint32_t bits_inc_embed = 6;
    constexpr uint32_t mask_embed = (1u << bits_embed) - 1;
    constexpr uint32_t mask_embed_end = (1u << bits_embed_end) - 1;
    constexpr uint32_t mask_loops = (1u << bits_loops) - 1;
    constexpr uint32_t mask_cond = (1u << bits_cond) - 1;
    constexpr uint32_t mask_dly = (1u << bits_dly) - 1;
    constexpr uint32_t mask_inc = (1u << bits_inc) - 1;
    constexpr uint32_t mask_inc_embed = (1u << bits_inc_embed) - 1;

    uint64_t bits = 0;
    uint32_t pos = 0;
    const uint8_t embed_end = (embed == 0) ? (embed_end_bits & mask_embed_end) : 0u;
    const uint8_t inc_embed = (embed == 0) ? (inc_embed_bits & mask_inc_embed) : 0u;

    bits |= static_cast<uint64_t>(inc_embed & mask_inc_embed) << pos; pos += bits_inc_embed;
    bits |= static_cast<uint64_t>(inc & mask_inc) << pos; pos += bits_inc;
    bits |= static_cast<uint64_t>(dly & mask_dly) << pos; pos += bits_dly;
    bits |= static_cast<uint64_t>(cond & mask_cond) << pos; pos += bits_cond;
    bits |= static_cast<uint64_t>(loops & mask_loops) << pos; pos += bits_loops;
    bits |= static_cast<uint64_t>(embed_end & mask_embed_end) << pos; pos += bits_embed_end;
    bits |= static_cast<uint64_t>(embed & mask_embed) << pos; pos += bits_embed;
    bits |= static_cast<uint64_t>(jump & 1u) << pos; pos += 1;
    bits |= static_cast<uint64_t>(vld & 1u) << pos;
    return bits;
}

std::vector<uint8_t> pack_msb_instruction(const std::vector<std::pair<uint32_t, uint64_t>>& fields) {
    uint32_t total_bits = 0;
    for (const auto& field : fields) {
        total_bits += field.first;
    }

    std::vector<uint8_t> bytes((total_bits + 7) / 8, 0);
    const uint32_t leading_pad_bits =
        static_cast<uint32_t>(bytes.size() * 8) - total_bits;
    uint32_t bit_pos = 0;
    for (const auto& field : fields) {
        const uint32_t width = field.first;
        const uint64_t value = field.second;
        for (uint32_t i = 0; i < width; ++i) {
            const uint32_t src_bit = width - 1 - i;
            const bool one = ((value >> src_bit) & 1ULL) != 0;
            if (!one) continue;
            const uint32_t dst_bit = leading_pad_bits + bit_pos + i;
            const uint32_t byte_idx = dst_bit / 8;
            const uint32_t bit_in_byte = 7 - (dst_bit % 8);
            bytes[byte_idx] |= static_cast<uint8_t>(1u << bit_in_byte);
        }
        bit_pos += width;
    }
    return bytes;
}

std::vector<std::pair<uint32_t, uint64_t>> make_lsu_header(uint8_t vld, uint8_t jump,
                                                           uint16_t loops,
                                                           uint8_t dly,
                                                           uint16_t inc) {
    return {
        {1, vld},
        {1, jump},
        {3, 0},      // EMBED
        {6, 0},      // EMBED_END
        {10, loops},
        {7, 0},      // COND
        {3, dly},
        {13, inc},
        {6, 0},      // INC_EMBED
    };
}

uint16_t encode_lsu_ur_addr(uint8_t bank, uint16_t addr, uint8_t byte_offset = 0) {
    return static_cast<uint16_t>(((bank & 0x7u) << 12) |
                                 ((addr & 0xFFu) << 4) |
                                 (byte_offset & 0xFu));
}

std::vector<uint8_t> make_stb_instruction(uint8_t smc_id_en, uint8_t tid,
                                          uint16_t ur_addr, uint16_t loops,
                                          uint16_t inc,
                                          uint8_t dly = 0,
                                          uint16_t line_byte_cnt = 0xFu) {
    auto fields = make_lsu_header(1, 0, loops, dly, inc);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {2, tid},
        {5, smc_id_en},
        {4, 0},       // inst_id
        {6, 0},       // line_stride
        {9, line_byte_cnt},  // byte count minus one
        {8, 0x1},     // ur_rd_line_cnt
        {15, ur_addr},
        {1, 1},       // vld
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_ldb_instruction(uint8_t smc_id_en, uint16_t ur_addr,
                                          uint16_t loops, uint16_t inc,
                                          uint8_t dly = 0,
                                          uint16_t line_byte_cnt = 0xFu) {
    auto fields = make_lsu_header(1, 0, loops, dly, inc);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {5, smc_id_en},
        {4, 0},       // inst_id
        {1, 0},       // post_fill
        {1, 0},       // pre_fill
        {6, 0},       // const
        {6, 0},       // line_stride: contiguous
        {9, line_byte_cnt},  // byte count minus one
        {8, 0x1},     // ur_wr_line_cnt
        {15, ur_addr},
        {1, 1},       // vld
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_stb_axi_instruction(uint8_t smc_id_en,
                                              uint64_t gr_base_addr,
                                              uint64_t gr_intlv_addr,
                                              uint16_t loops,
                                              uint16_t inc,
                                              uint8_t dly = 0,
                                              uint8_t brst = 0,
                                              uint8_t first_byte_strb = 0,
                                              uint8_t last_byte_strb = 0) {
    auto fields = make_lsu_header(1, 0, loops, dly, inc);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {5, smc_id_en},
        {4, 0},                         // inst_id
        {40, gr_intlv_addr},
        {40, gr_base_addr},
        {4, brst},
        {4, last_byte_strb},            // 0 means full 16B
        {4, first_byte_strb},           // 0 means full 16B
        {1, 1},                         // vld
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_ldb_axi_instruction(uint8_t smc_id_en,
                                              uint64_t gr_base_addr,
                                              uint64_t gr_intlv_addr,
                                              uint16_t loops,
                                              uint16_t inc,
                                              uint8_t dly = 0,
                                              uint8_t brst = 0,
                                              uint8_t tid = 0,
                                              uint8_t first_byte_strb = 0,
                                              uint8_t last_byte_strb = 0) {
    auto fields = make_lsu_header(1, 0, loops, dly, inc);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {2, tid},
        {5, smc_id_en},
        {4, 0},                         // inst_id
        {40, gr_intlv_addr},
        {40, gr_base_addr},
        {4, brst},
        {4, last_byte_strb},            // 0 means full 16B
        {4, first_byte_strb},           // 0 means full 16B
        {1, 1},                         // vld
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_stb_axi_idle_instruction() {
    auto fields = make_lsu_header(1, 0, 0, 0, 0);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {5, 0}, {4, 0}, {40, 0}, {40, 0}, {4, 0}, {4, 0}, {4, 0}, {1, 0},
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_ldb_axi_end_instruction() {
    auto fields = make_lsu_header(1, 1, 0, 0, 0);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {2, 0}, {5, 0}, {4, 0}, {40, 0}, {40, 0}, {4, 0}, {4, 0}, {4, 0}, {1, 0},
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_stb_axi_end_instruction() {
    auto fields = make_lsu_header(1, 1, 0, 0, 0);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {5, 0}, {4, 0}, {40, 0}, {40, 0}, {4, 0}, {4, 0}, {4, 0}, {1, 0},
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_stb_end_instruction() {
    auto fields = make_lsu_header(1, 1, 0, 0, 0);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {2, 0}, {5, 0}, {4, 0}, {6, 0}, {9, 0}, {8, 0}, {15, 0}, {1, 0},
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_stb_idle_instruction() {
    auto fields = make_lsu_header(1, 0, 0, 0, 0);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {2, 0}, {5, 0}, {4, 0}, {6, 0}, {9, 0}, {8, 0}, {15, 0}, {1, 0},
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_ldb_end_instruction() {
    auto fields = make_lsu_header(1, 1, 0, 0, 0);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {5, 0}, {4, 0}, {1, 0}, {1, 0}, {6, 0}, {6, 0},
        {9, 0}, {8, 0}, {15, 0}, {1, 0},
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

std::vector<uint8_t> make_ldb_idle_instruction() {
    auto fields = make_lsu_header(1, 0, 0, 0, 0);
    const std::vector<std::pair<uint32_t, uint64_t>> code = {
        {5, 0}, {4, 0}, {1, 0}, {1, 0}, {6, 0}, {6, 0},
        {9, 0}, {8, 0}, {15, 0}, {1, 0},
    };
    fields.insert(fields.end(), code.begin(), code.end());
    return pack_msb_instruction(fields);
}

uint8_t byte_count_to_strb_code(uint16_t byte_count);
uint16_t segment_src_lsu_addr(const SelectIntCopySegment& seg);
uint16_t segment_dst_lsu_addr(const SelectIntCopySegment& seg);
bool segment_partial_byte_count(const SelectIntCopySegment& seg,
                                uint16_t& byte_count);

std::vector<IqInstructionFile> select_int_load(const SelectIntOpParams& params) {
    std::vector<IqInstructionFile> files;

    IqInstructionFile ldb_axi_iq;
    ldb_axi_iq.para_iq_id = kV419IqIdLdbAxi;
    ldb_axi_iq.para_iq_name = params.para_use_ddr ? "ldb_axi_load" : "ldb_axi";
    ldb_axi_iq.para_width_bits = 154;

    IqInstructionFile ldb_iq;
    ldb_iq.para_iq_id = kV419IqIdLdb;
    ldb_iq.para_iq_name = params.para_use_ddr ? "ldb_load" : iq::IQ_NAME_LDB;
    ldb_iq.para_width_bits = 106;

    for (const auto& segment : params.para_segments) {
        if (segment.para_dst_byte_enable != 0) {
            uint16_t bytes = 0;
            if (!segment_partial_byte_count(segment, bytes)) {
                continue;
            }

            (void)bytes;
            const uint64_t src_ddr =
                params.para_input_ddr_base_addr +
                (segment.para_src_byte_offset / kUrWordBytes) * kUrWordBytes;
            const uint16_t ur_addr =
                encode_lsu_ur_addr(segment.para_src_bank, segment.para_src_addr);

            auto ldb_axi_inst = make_ldb_axi_instruction(
                params.para_smc_id_en, src_ddr, params.para_ddr_intlv_addr,
                0, 0, 0, 0);
            auto ldb_inst = make_ldb_instruction(
                params.para_smc_id_en, ur_addr, 0, 0);

            ldb_axi_iq.para_data.insert(ldb_axi_iq.para_data.end(),
                                        ldb_axi_inst.begin(), ldb_axi_inst.end());
            ldb_iq.para_data.insert(ldb_iq.para_data.end(),
                                    ldb_inst.begin(), ldb_inst.end());
            continue;
        }

        uint32_t emitted = 0;
        while (emitted < segment.para_words) {
            const uint32_t remaining = segment.para_words - emitted;
            const uint32_t chunk_words =
                std::min(remaining,
                         std::min(params.para_loop_chunk_words,
                                  kMaxAxiBurstWords));
            const uint16_t loops = static_cast<uint16_t>(chunk_words - 1);
            const uint16_t inc = (chunk_words > 1) ? 0x10u : 0u;
            const uint8_t axi_brst = static_cast<uint8_t>(chunk_words - 1);
            const uint64_t src_ddr =
                params.para_input_ddr_base_addr +
                segment.para_src_byte_offset +
                static_cast<uint64_t>(emitted) * kUrWordBytes;
            const uint16_t ur_addr =
                encode_lsu_ur_addr(segment.para_src_bank,
                                   static_cast<uint16_t>(segment.para_src_addr + emitted));

            auto ldb_axi_inst = make_ldb_axi_instruction(
                params.para_smc_id_en, src_ddr, params.para_ddr_intlv_addr,
                0, 0, 0, axi_brst);
            auto ldb_inst = make_ldb_instruction(
                params.para_smc_id_en, ur_addr, loops, inc);

            ldb_axi_iq.para_data.insert(ldb_axi_iq.para_data.end(),
                                        ldb_axi_inst.begin(), ldb_axi_inst.end());
            ldb_iq.para_data.insert(ldb_iq.para_data.end(),
                                    ldb_inst.begin(), ldb_inst.end());
            emitted += chunk_words;
        }
    }

    auto ldb_axi_end = make_ldb_axi_end_instruction();
    auto ldb_end = make_ldb_end_instruction();
    ldb_axi_iq.para_data.insert(ldb_axi_iq.para_data.end(),
                                ldb_axi_end.begin(), ldb_axi_end.end());
    ldb_iq.para_data.insert(ldb_iq.para_data.end(),
                            ldb_end.begin(), ldb_end.end());

    files.push_back(std::move(ldb_axi_iq));
    files.push_back(std::move(ldb_iq));
    return files;
}

std::vector<IqInstructionFile> select_int_store(const SelectIntOpParams& params) {
    std::vector<IqInstructionFile> files;

    IqInstructionFile stb_iq;
    stb_iq.para_iq_id = kV419IqIdStb;
    stb_iq.para_iq_name = params.para_use_ddr ? "stb_store" : iq::IQ_NAME_STB;
    stb_iq.para_width_bits = 100;

    IqInstructionFile stb_axi_iq;
    stb_axi_iq.para_iq_id = kV419IqIdStbAxi;
    stb_axi_iq.para_iq_name = params.para_use_ddr ? "stb_axi_store" : "stb_axi";
    stb_axi_iq.para_width_bits = 152;

    auto stb_idle = make_stb_idle_instruction();
    auto stb_axi_idle = make_stb_axi_idle_instruction();
    const uint32_t store_delay =
        std::min<uint32_t>(params.para_ddr_store_delay_insts, 256);
    for (uint32_t i = 0; i < store_delay; ++i) {
        stb_iq.para_data.insert(stb_iq.para_data.end(),
                                stb_idle.begin(), stb_idle.end());
        stb_axi_iq.para_data.insert(stb_axi_iq.para_data.end(),
                                    stb_axi_idle.begin(), stb_axi_idle.end());
    }

    for (const auto& segment : params.para_segments) {
        if (segment.para_dst_byte_enable != 0) {
            uint16_t bytes = 0;
            if (!segment_partial_byte_count(segment, bytes)) {
                continue;
            }

            const uint16_t line_byte_cnt = static_cast<uint16_t>(bytes - 1);
            const uint8_t byte_strb = byte_count_to_strb_code(bytes);
            const uint64_t dst_ddr =
                params.para_output_ddr_base_addr + segment.para_dst_byte_offset;
            // DDR partial fallback: source was loaded by aligned LDBAXI into
            // source UR. Store the selected bytes directly from source UR to
            // DDR, avoiding unaligned LDBAXI and not depending on packed UR.
            const uint16_t ur_addr = params.para_use_ddr
                ? segment_src_lsu_addr(segment)
                : segment_dst_lsu_addr(segment);

            auto stb_inst = make_stb_instruction(
                params.para_smc_id_en,
                2,  // tid=2: STB stream goes to STBAXI/DDR.
                ur_addr,
                0,
                0,
                0,
                line_byte_cnt);
            auto stb_axi_inst = make_stb_axi_instruction(
                params.para_smc_id_en, dst_ddr, params.para_ddr_intlv_addr,
                0, 0, 0, 0, byte_strb, byte_strb);

            stb_iq.para_data.insert(stb_iq.para_data.end(),
                                    stb_inst.begin(), stb_inst.end());
            stb_axi_iq.para_data.insert(stb_axi_iq.para_data.end(),
                                        stb_axi_inst.begin(), stb_axi_inst.end());
            auto stb_idle_local = make_stb_idle_instruction();
            auto stb_axi_idle_local = make_stb_axi_idle_instruction();
            for (int pad = 0; pad < 3; ++pad) {
                stb_iq.para_data.insert(stb_iq.para_data.end(),
                                        stb_idle_local.begin(),
                                        stb_idle_local.end());
                stb_axi_iq.para_data.insert(stb_axi_iq.para_data.end(),
                                            stb_axi_idle_local.begin(),
                                            stb_axi_idle_local.end());
            }
            continue;
        }

        uint32_t emitted = 0;
        while (emitted < segment.para_words) {
            const uint32_t remaining = segment.para_words - emitted;
            const uint32_t chunk_words =
                std::min(remaining,
                         std::min(params.para_loop_chunk_words,
                                  kMaxAxiBurstWords));
            const uint16_t loops = static_cast<uint16_t>(chunk_words - 1);
            const uint16_t inc = (chunk_words > 1) ? 0x10u : 0u;
            const uint8_t axi_brst = static_cast<uint8_t>(chunk_words - 1);
            const uint64_t dst_ddr =
                params.para_output_ddr_base_addr +
                segment.para_dst_byte_offset +
                static_cast<uint64_t>(emitted) * kUrWordBytes;
            const uint16_t ur_addr =
                encode_lsu_ur_addr(segment.para_dst_bank,
                                   static_cast<uint16_t>(segment.para_dst_addr + emitted));

            auto stb_inst = make_stb_instruction(
                params.para_smc_id_en,
                2,  // tid=2: STB stream goes to STBAXI/DDR.
                ur_addr,
                loops,
                inc);
            auto stb_axi_inst = make_stb_axi_instruction(
                params.para_smc_id_en, dst_ddr, params.para_ddr_intlv_addr,
                0, 0, 0, axi_brst);

            stb_iq.para_data.insert(stb_iq.para_data.end(),
                                    stb_inst.begin(), stb_inst.end());
            stb_axi_iq.para_data.insert(stb_axi_iq.para_data.end(),
                                        stb_axi_inst.begin(), stb_axi_inst.end());
            emitted += chunk_words;
        }
    }

    auto stb_end = make_stb_end_instruction();
    auto stb_axi_end = make_stb_axi_end_instruction();
    stb_iq.para_data.insert(stb_iq.para_data.end(),
                            stb_end.begin(), stb_end.end());
    stb_axi_iq.para_data.insert(stb_axi_iq.para_data.end(),
                                stb_axi_end.begin(), stb_axi_end.end());

    files.push_back(std::move(stb_iq));
    files.push_back(std::move(stb_axi_iq));
    return files;
}

std::vector<IqInstructionFile> select_int_select(const SelectIntOpParams& params) {
    std::vector<IqInstructionFile> files;

    IqInstructionFile stb_iq;
    stb_iq.para_iq_id = kV419IqIdStb;
    stb_iq.para_iq_name = params.para_use_ddr ? "stb_select" : iq::IQ_NAME_STB;
    stb_iq.para_width_bits = 100;

    IqInstructionFile ldb_iq;
    ldb_iq.para_iq_id = kV419IqIdLdb;
    ldb_iq.para_iq_name = params.para_use_ddr ? "ldb_select" : iq::IQ_NAME_LDB;
    ldb_iq.para_width_bits = 106;

    for (size_t segment_idx = 0; segment_idx < params.para_segments.size(); ++segment_idx) {
        const auto& segment = params.para_segments[segment_idx];
        if (segment.para_dst_byte_enable != 0) {
            uint16_t bytes = 0;
            if (!segment_partial_byte_count(segment, bytes)) {
                continue;
            }

            const uint16_t src_start = segment_src_lsu_addr(segment);
            const uint16_t dst_start = segment_dst_lsu_addr(segment);
            constexpr uint8_t kPartialLsuDly = 7;
            const uint16_t line_byte_cnt = static_cast<uint16_t>(bytes - 1);

            auto stb_inst = make_stb_instruction(
                params.para_smc_id_en,
                0,  // tid=0: direct UR STB -> LDB stream, no STBAXI.
                src_start,
                0,
                0,
                kPartialLsuDly,
                line_byte_cnt);
            auto ldb_inst = make_ldb_instruction(
                params.para_smc_id_en,
                dst_start,
                0,
                0,
                kPartialLsuDly,
                line_byte_cnt);
            stb_iq.para_data.insert(stb_iq.para_data.end(), stb_inst.begin(), stb_inst.end());
            ldb_iq.para_data.insert(ldb_iq.para_data.end(), ldb_inst.begin(), ldb_inst.end());

            if (segment_idx + 1 < params.para_segments.size()) {
                auto stb_idle = make_stb_idle_instruction();
                auto ldb_idle = make_ldb_idle_instruction();
                for (int pad = 0; pad < 3; ++pad) {
                    stb_iq.para_data.insert(stb_iq.para_data.end(),
                                            stb_idle.begin(), stb_idle.end());
                    ldb_iq.para_data.insert(ldb_iq.para_data.end(),
                                            ldb_idle.begin(), ldb_idle.end());
                }
            }
            continue;
        }

        uint32_t emitted = 0;
        while (emitted < segment.para_words) {
            const uint32_t remaining = segment.para_words - emitted;
            const uint32_t chunk_words = std::min(remaining, params.para_loop_chunk_words);
            const uint16_t loops = static_cast<uint16_t>(chunk_words - 1);
            const uint16_t inc = (chunk_words > 1) ? 0x10u : 0u;
            const uint16_t src_addr = static_cast<uint16_t>(segment.para_src_addr + emitted);
            const uint16_t dst_addr = static_cast<uint16_t>(segment.para_dst_addr + emitted);

            auto stb_inst = make_stb_instruction(
                params.para_smc_id_en,
                0,  // tid=0: direct UR STB -> LDB stream, no STBAXI.
                encode_lsu_ur_addr(segment.para_src_bank, src_addr),
                loops,
                inc);
            auto ldb_inst = make_ldb_instruction(
                params.para_smc_id_en,
                encode_lsu_ur_addr(segment.para_dst_bank, dst_addr),
                loops,
                inc);

            stb_iq.para_data.insert(stb_iq.para_data.end(), stb_inst.begin(), stb_inst.end());
            ldb_iq.para_data.insert(ldb_iq.para_data.end(), ldb_inst.begin(), ldb_inst.end());
            emitted += chunk_words;
        }
    }

    auto stb_end = make_stb_end_instruction();
    auto ldb_end = make_ldb_end_instruction();
    stb_iq.para_data.insert(stb_iq.para_data.end(), stb_end.begin(), stb_end.end());
    ldb_iq.para_data.insert(ldb_iq.para_data.end(), ldb_end.begin(), ldb_end.end());

    files.push_back(std::move(stb_iq));
    files.push_back(std::move(ldb_iq));
    return files;
}

std::vector<uint8_t> make_small_code_instruction(uint8_t vld, uint8_t jump, uint16_t loops,
                                                 uint8_t dly, uint8_t inc,
                                                 uint64_t code, uint16_t code_bits) {
    const uint64_t header = serialize_header_bits(vld, jump, 0, loops, 0, dly, inc, 0, 0);
    const uint64_t code_mask = (code_bits >= 64) ? ~0ULL : ((1ULL << code_bits) - 1ULL);
    const uint64_t packed = (header << code_bits) | (code & code_mask);
    const int total_width = 41 + code_bits;
    const int num_bytes = (total_width + 7) / 8;

    std::vector<uint8_t> bytes;
    for (int i = num_bytes - 1; i >= 0; --i) {
        bytes.push_back(static_cast<uint8_t>((packed >> (i * 8)) & 0xFF));
    }
    return bytes;
}

std::vector<uint8_t> make_ur_wdata_instruction(uint8_t vld, uint8_t jump, uint16_t loops,
                                               uint8_t dly, uint8_t inc, uint64_t code_33) {
    const uint64_t header = serialize_header_bits(vld, jump, 0, loops, 0, dly, inc, 0, 0);
    const unsigned __int128 packed =
        (static_cast<unsigned __int128>(header) << 33) |
        static_cast<unsigned __int128>(code_33 & 0x1FFFFFFFFULL);

    std::vector<uint8_t> bytes;
    for (int i = 9; i >= 0; --i) {
        bytes.push_back(static_cast<uint8_t>((packed >> (i * 8)) & 0xFF));
    }
    return bytes;
}

std::vector<uint8_t> serialize_tensor(const app::AppTensorDesc& tensor) {
    std::vector<uint8_t> data;
    if (tensor.para_dtype == app::AppDataType::INT32) {
        data.reserve(tensor.para_data_i32.size() * sizeof(uint32_t));
        for (int32_t v : tensor.para_data_i32) {
            const uint32_t bits = static_cast<uint32_t>(v);
            data.push_back(bits & 0xFF);
            data.push_back((bits >> 8) & 0xFF);
            data.push_back((bits >> 16) & 0xFF);
            data.push_back((bits >> 24) & 0xFF);
        }
        return data;
    }

    if (tensor.para_dtype == app::AppDataType::FLOAT16) {
        data.reserve(tensor.para_data_f32.size() * sizeof(uint16_t));
        for (float v : tensor.para_data_f32) {
            const uint16_t bits = app::float32_to_float16(v);
            data.push_back(bits & 0xFF);
            data.push_back((bits >> 8) & 0xFF);
        }
        return data;
    }

    data.reserve(tensor.para_data_f32.size() * sizeof(uint32_t));
    for (float v : tensor.para_data_f32) {
        uint32_t bits = 0;
        std::memcpy(&bits, &v, sizeof(float));
        data.push_back(bits & 0xFF);
        data.push_back((bits >> 8) & 0xFF);
        data.push_back((bits >> 16) & 0xFF);
        data.push_back((bits >> 24) & 0xFF);
    }
    return data;
}

// Compute 16-bit write-byte-enable mask for an element at byte position `pos`
// within a 16-byte word, occupying `elem_bytes` bytes.
uint16_t compute_byte_enable(uint8_t pos, size_t elem_bytes) {
    uint16_t mask = 0;
    for (size_t b = 0; b < elem_bytes; ++b) {
        mask |= static_cast<uint16_t>(1u << (pos + b));
    }
    return mask;
}

uint16_t count_byte_enable(uint16_t mask) {
    uint16_t count = 0;
    while (mask != 0) {
        count = static_cast<uint16_t>(count + (mask & 1u));
        mask = static_cast<uint16_t>(mask >> 1);
    }
    return count;
}

uint8_t byte_count_to_strb_code(uint16_t byte_count) {
    if (byte_count >= kUrWordBytes) return 0;
    return static_cast<uint8_t>(byte_count & 0xFu);
}

uint16_t segment_src_lsu_addr(const SelectIntCopySegment& seg) {
    return encode_lsu_ur_addr(
        seg.para_src_bank,
        seg.para_src_addr,
        static_cast<uint8_t>(seg.para_src_byte_offset % kUrWordBytes));
}

uint16_t segment_dst_lsu_addr(const SelectIntCopySegment& seg) {
    return encode_lsu_ur_addr(
        seg.para_dst_bank,
        seg.para_dst_addr,
        static_cast<uint8_t>(seg.para_dst_byte_offset % kUrWordBytes));
}

bool segment_partial_byte_count(const SelectIntCopySegment& seg,
                                uint16_t& byte_count) {
    if (seg.para_dst_byte_enable == 0) return false;
    byte_count = count_byte_enable(seg.para_dst_byte_enable);
    const uint8_t src_byte_pos = static_cast<uint8_t>(
        seg.para_src_byte_offset % kUrWordBytes);
    const uint8_t dst_byte_pos = static_cast<uint8_t>(
        seg.para_dst_byte_offset % kUrWordBytes);
    return byte_count != 0 &&
           src_byte_pos + byte_count <= kUrWordBytes &&
           dst_byte_pos + byte_count <= kUrWordBytes;
}

// v4.18 UR write code is 33 bits:
// [32:28] = smc_id_en, [27:12] = ur_wbe_0, [11:4] = ur_waddr_0,
// [3:1] = ur_wbank_0, [0] = ur_we_0.
uint64_t encode_ur_write_code(uint8_t bank, uint16_t addr, uint16_t wbe, uint8_t smc_id_en) {
    return (static_cast<uint64_t>(smc_id_en & 0x1Fu) << 28) |
           (static_cast<uint64_t>(wbe) << 12) |
           (static_cast<uint64_t>(addr & 0xFFu) << 4) |
           (static_cast<uint64_t>(bank & 0x7u) << 1) |
           0x1u;
}

// Encode LOGIC IQ code: {valid[1], op_type[4], prec[1]} = 6 bits
// Layout matches simulator decode: valid at bit 5, op_type at bits [4:1], prec at bit 0
// prec: 0=16bit, 1=32bit
uint8_t encode_logic_code(uint8_t op_type, bool is_32bit) {
    return static_cast<uint8_t>(0x20u | ((op_type & 0xFu) << 1) | (is_32bit ? 1u : 0u));
}

// Build a single-element segment for non-contiguous inner dimensions.
// Determines if LOGIC shift is needed (src_pos != dst_pos within word).
bool build_element_segment(uint64_t src_byte, uint64_t dst_byte,
                           size_t elem_bytes,
                           const SelectIntOpParams& params,
                           SelectIntCopySegment& seg) {
    const uint64_t src_word = src_byte / kUrWordBytes;
    const uint64_t dst_word = dst_byte / kUrWordBytes;
    const uint8_t src_pos = static_cast<uint8_t>(src_byte % kUrWordBytes);
    const uint8_t dst_pos = static_cast<uint8_t>(dst_byte % kUrWordBytes);
    if (src_pos + elem_bytes > kUrWordBytes ||
        dst_pos + elem_bytes > kUrWordBytes) {
        return false;
    }

    const bool src_ok =
        map_linear_word_to_bank_addr(params.para_input_bank, params.para_input_addr,
                                     params.para_input_bank_stride_words, src_word,
                                     seg.para_src_bank, seg.para_src_addr) &&
        segment_range_fits_bank(seg.para_src_addr, 1, params.para_input_bank_stride_words);

    if (!src_ok ||
        !map_linear_word_to_bank_addr(params.para_output_bank, params.para_output_addr,
                                      params.para_output_bank_stride_words, dst_word,
                                      seg.para_dst_bank, seg.para_dst_addr)) {
        return false;
    }
    if (!segment_range_fits_bank(seg.para_dst_addr, 1, params.para_output_bank_stride_words)) {
        return false;
    }

    seg.para_words = 1;
    seg.para_src_byte_offset = static_cast<size_t>(src_byte);
    seg.para_dst_byte_offset = static_cast<size_t>(dst_byte);
    seg.para_dst_byte_enable = compute_byte_enable(dst_pos, elem_bytes);

    if (!params.para_use_ldb_stb && !params.para_use_ddr) {
        // Partial element select must use the byte-addressed STB/LDB path.
        // Do not extend the legacy UVR_WDATA0 direct path.
        return false;
    }

    return true;
}

void build_legacy_contiguous_plan(SelectIntOpParams& params,
                                  const app::AppTensorDesc* input,
                                  const app::AppOperatorRequest& req) {
    uint32_t default_words = 1;
    if (input != nullptr) default_words = words_for_input(*input, req);
    params.para_select_words = static_cast<uint32_t>(
        get_i32_param(req, "select_words", get_i32_param(req, "num_words", default_words)));
    if (params.para_select_words == 0) params.para_select_words = default_words;

    SelectIntCopySegment seg;
    seg.para_words = params.para_select_words;
    const uint64_t src_linear_word = params.para_source_offset_words;
    const uint64_t dst_linear_word = 0;
    const bool src_ok =
        map_linear_word_to_bank_addr(params.para_input_bank, params.para_input_addr,
                                     params.para_input_bank_stride_words, src_linear_word,
                                     seg.para_src_bank, seg.para_src_addr) &&
        segment_range_fits_bank(seg.para_src_addr, seg.para_words,
                                params.para_input_bank_stride_words);
    if (!src_ok ||
        !map_linear_word_to_bank_addr(params.para_output_bank, params.para_output_addr,
                                      params.para_output_bank_stride_words, dst_linear_word,
                                      seg.para_dst_bank, seg.para_dst_addr) ||
        !segment_range_fits_bank(seg.para_dst_addr, seg.para_words,
                                 params.para_output_bank_stride_words)) {
        mark_unsupported(params, "SelectInt UR address range exceeds 8-bit UR address field");
        return;
    }
    seg.para_src_byte_offset = static_cast<size_t>(params.para_source_offset_words) * kUrWordBytes;
    seg.para_dst_byte_offset = 0;
    params.para_segments = {seg};
    params.para_output_bytes = params.para_select_words * kUrWordBytes;
}

// Build contiguous inner-dim plan (original fast path).
// Each outer iteration produces one bulk-copy segment.
bool build_contiguous_select_plan(SelectIntOpParams& params,
                                  const std::vector<int64_t>& shape,
                                  const std::vector<int64_t>& stride,
                                  int64_t dim,
                                  int64_t index,
                                  int64_t storage_offset,
                                  size_t elem_bytes) {
    const size_t dim_u = static_cast<size_t>(dim);
    const uint64_t inner_elems = product_dims(shape, dim_u + 1, shape.size());
    const uint64_t outer_elems = product_dims(shape, 0, dim_u);
    const uint64_t block_bytes = inner_elems * elem_bytes;

    if (block_bytes == 0 || (block_bytes % kUrWordBytes) != 0) {
        mark_unsupported(params, "SelectInt requires each selected block to be 16-byte aligned in size");
        return false;
    }

    params.para_segments.clear();
    params.para_segments.reserve(static_cast<size_t>(outer_elems));
    const uint32_t words_per_block = static_cast<uint32_t>(block_bytes / kUrWordBytes);
    for (uint64_t outer = 0; outer < outer_elems; ++outer) {
        uint64_t rem = outer;
        int64_t src_elem = storage_offset + index * stride[dim_u];
        for (int d = static_cast<int>(dim_u) - 1; d >= 0; --d) {
            const int64_t coord = static_cast<int64_t>(rem % static_cast<uint64_t>(shape[static_cast<size_t>(d)]));
            rem /= static_cast<uint64_t>(shape[static_cast<size_t>(d)]);
            src_elem += coord * stride[static_cast<size_t>(d)];
        }

        const uint64_t src_byte = static_cast<uint64_t>(src_elem) * elem_bytes;
        const uint64_t dst_byte = outer * block_bytes;
        if ((src_byte % kUrWordBytes) != 0 || (dst_byte % kUrWordBytes) != 0) {
            mark_unsupported(params, "SelectInt requires 16-byte aligned source and output blocks");
            return false;
        }

        SelectIntCopySegment seg;
        seg.para_words = words_per_block;
        const uint64_t src_linear_word = src_byte / kUrWordBytes;
        const uint64_t dst_linear_word = dst_byte / kUrWordBytes;
        const bool src_ok =
            map_linear_word_to_bank_addr(params.para_input_bank, params.para_input_addr,
                                         params.para_input_bank_stride_words, src_linear_word,
                                         seg.para_src_bank, seg.para_src_addr) &&
            segment_range_fits_bank(seg.para_src_addr, seg.para_words,
                                    params.para_input_bank_stride_words);
        if (!src_ok ||
            !map_linear_word_to_bank_addr(params.para_output_bank, params.para_output_addr,
                                          params.para_output_bank_stride_words, dst_linear_word,
                                          seg.para_dst_bank, seg.para_dst_addr) ||
            !segment_range_fits_bank(seg.para_dst_addr, seg.para_words,
                                     params.para_output_bank_stride_words)) {
            mark_unsupported(params, "SelectInt UR address range exceeds 8-bit UR address field");
            return false;
        }
        seg.para_src_byte_offset = static_cast<size_t>(src_byte);
        seg.para_dst_byte_offset = static_cast<size_t>(dst_byte);
        params.para_segments.push_back(seg);
    }

    params.para_select_words = static_cast<uint32_t>(outer_elems * words_per_block);
    params.para_output_bytes = static_cast<uint32_t>(outer_elems * block_bytes);
    return true;
}

// Build non-contiguous inner-dim plan.
// Processes elements grouped by destination word.  When the source byte position
// differs from the destination position, marks the segment for LOGIC shifting.
bool build_noncontiguous_select_plan(SelectIntOpParams& params,
                                     const std::vector<int64_t>& shape,
                                     const std::vector<int64_t>& stride,
                                     int64_t dim,
                                     int64_t index,
                                     int64_t storage_offset,
                                     size_t elem_bytes) {
    const size_t dim_u = static_cast<size_t>(dim);
    const uint64_t inner_elems = product_dims(shape, dim_u + 1, shape.size());
    const uint64_t outer_elems = product_dims(shape, 0, dim_u);

    if (inner_elems == 0) {
        mark_unsupported(params, "SelectInt empty inner dimensions");
        return false;
    }

    // Precompute inner-dim coordinates → relative element offset within block.
    // inner_coord[i] decomposes linear index i into (dim+1..rank-1) coordinates.
    std::vector<int64_t> inner_strides_for_decomp;
    {
        int64_t stride_acc = 1;
        for (int d = static_cast<int>(shape.size()) - 1; d > static_cast<int>(dim_u); --d) {
            inner_strides_for_decomp.push_back(stride_acc);
            stride_acc *= shape[static_cast<size_t>(d)];
        }
        std::reverse(inner_strides_for_decomp.begin(), inner_strides_for_decomp.end());
    }

    // Total output bytes
    const uint64_t total_output_elems = outer_elems * inner_elems;
    const uint64_t total_output_bytes = total_output_elems * elem_bytes;

    params.para_segments.clear();
    params.para_segments.reserve(static_cast<size_t>(total_output_elems));
    params.para_has_logic_segments = false;

    for (uint64_t outer = 0; outer < outer_elems; ++outer) {
        // Compute outer coordinate contribution
        uint64_t rem = outer;
        int64_t outer_elem_base = storage_offset + index * stride[dim_u];
        for (int d = static_cast<int>(dim_u) - 1; d >= 0; --d) {
            const int64_t coord = static_cast<int64_t>(rem % static_cast<uint64_t>(shape[static_cast<size_t>(d)]));
            rem /= static_cast<uint64_t>(shape[static_cast<size_t>(d)]);
            outer_elem_base += coord * stride[static_cast<size_t>(d)];
        }

        const uint64_t dst_block_byte = outer * inner_elems * elem_bytes;

        for (uint64_t inner = 0; inner < inner_elems; ++inner) {
            // Decompose inner index into coordinates
            int64_t inner_elem_offset = 0;
            {
                uint64_t t = inner;
                for (int d = static_cast<int>(shape.size()) - 1;
                     d > static_cast<int>(dim_u);
                     --d) {
                    const int64_t inner_dim_size = shape[static_cast<size_t>(d)];
                    const int64_t coord = static_cast<int64_t>(t % static_cast<uint64_t>(inner_dim_size));
                    t /= static_cast<uint64_t>(inner_dim_size);
                    inner_elem_offset += coord * stride[static_cast<size_t>(d)];
                }
            }

            const int64_t src_elem = outer_elem_base + inner_elem_offset;
            const uint64_t src_byte = static_cast<uint64_t>(src_elem) * elem_bytes;
            const uint64_t dst_byte = dst_block_byte + inner * elem_bytes;

            SelectIntCopySegment seg;
            if (!build_element_segment(src_byte, dst_byte, elem_bytes, params, seg)) {
                mark_unsupported(params, "SelectInt UR address range exceeded for non-contiguous element");
                return false;
            }
            if (seg.para_needs_logic_shift) {
                params.para_has_logic_segments = true;
            }
            params.para_segments.push_back(seg);
        }
    }

    params.para_select_words = static_cast<uint32_t>(params.para_segments.size());
    params.para_output_bytes = static_cast<uint32_t>(total_output_bytes);
    return true;
}

// Build shift-amount LUT in UR: one 16-byte word per unique (right, bytes) pair.
// The word encodes the shift amount as a 32-bit little-endian value in lanes 0..3.
void build_shift_lut(SelectIntOpParams& params) {
    std::set<std::pair<bool, uint8_t>> unique_shifts;
    for (const auto& seg : params.para_segments) {
        if (seg.para_needs_logic_shift) {
            unique_shifts.insert({seg.para_shift_right, seg.para_shift_bytes});
        }
    }
    if (unique_shifts.empty()) return;

    // Allocate LUT in bank 1 (loaded via other_bin at address 0).
    params.para_shift_lut_bank = 1;
    params.para_shift_lut_addr = 0;
    params.para_shift_lut_data.resize(unique_shifts.size() * kUrWordBytes, 0);

    size_t idx = 0;
    for (const auto& sh : unique_shifts) {
        // Map each segment to its LUT address
        for (auto& seg : params.para_segments) {
            if (seg.para_needs_logic_shift &&
                seg.para_shift_right == sh.first &&
                seg.para_shift_bytes == sh.second) {
                seg.para_logic_s1_bank = params.para_shift_lut_bank;
                seg.para_logic_s1_addr = static_cast<uint16_t>(
                    params.para_shift_lut_addr + static_cast<uint32_t>(idx));
            }
        }

        // Shift amount in BITS (S1 lanes expect bit count for logic_shift).
        uint32_t shift_val = static_cast<uint32_t>(sh.second) * 8u;
        // lane 0: shift amount for 32-bit mode, lane 0-3 for 16-bit mode
        for (int lane = 0; lane < 4; ++lane) {
            size_t byte_off = idx * kUrWordBytes + lane * 4;
            params.para_shift_lut_data[byte_off + 0] = static_cast<uint8_t>(shift_val & 0xFF);
            params.para_shift_lut_data[byte_off + 1] = static_cast<uint8_t>((shift_val >> 8) & 0xFF);
            params.para_shift_lut_data[byte_off + 2] = static_cast<uint8_t>((shift_val >> 16) & 0xFF);
            params.para_shift_lut_data[byte_off + 3] = static_cast<uint8_t>((shift_val >> 24) & 0xFF);
        }
        ++idx;
    }
}

void build_pytorch_select_plan(SelectIntOpParams& params,
                               const app::AppTensorDesc& input,
                               const app::AppOperatorRequest& req) {
    const std::vector<int64_t> shape = tensor_shape_i64(input, req);
    if (shape.empty()) {
        mark_unsupported(params, "SelectInt requires a non-scalar input tensor");
        return;
    }

    int64_t dim = static_cast<int64_t>(get_i32_param(req, "dim", 0));
    const int64_t rank = static_cast<int64_t>(shape.size());
    if (dim < 0) dim += rank;
    if (dim < 0 || dim >= rank) {
        mark_unsupported(params, "SelectInt dim is out of range");
        return;
    }

    int64_t index = static_cast<int64_t>(get_i32_param(req, "index", 0));
    const int64_t dim_size = shape[static_cast<size_t>(dim)];
    if (index < 0) index += dim_size;
    if (index < 0 || index >= dim_size) {
        mark_unsupported(params, "SelectInt index is out of range");
        return;
    }

    std::vector<int64_t> stride = get_i32_array_param(req, "input_stride");
    if (stride.empty()) stride = get_i32_array_param(req, "stride");
    if (stride.empty()) stride = contiguous_stride(shape);
    if (stride.size() != shape.size()) {
        mark_unsupported(params, "SelectInt stride rank does not match input shape rank");
        return;
    }

    int64_t storage_offset = static_cast<int64_t>(get_i32_param(req, "storage_offset", 0));
    // Normalize negative storage_offset: add total element count.
    if (storage_offset < 0) {
        uint64_t total_elems = 1;
        for (auto s : shape) total_elems *= static_cast<uint64_t>(s);
        storage_offset += static_cast<int64_t>(total_elems);
        if (storage_offset < 0) {
            mark_unsupported(params, "SelectInt storage_offset normalization overflow");
            return;
        }
    }

    const size_t elem_bytes = bytes_per_element(input.para_dtype);
    const size_t dim_u = static_cast<size_t>(dim);

    // Check if inner dimensions are contiguous (common case → fast path).
    if (inner_dims_are_contiguous(shape, stride, dim_u)) {
        if (build_contiguous_select_plan(params, shape, stride, dim, index,
                                          storage_offset, elem_bytes)) {
            return;
        }
        // Contiguous plan failed (e.g. block size not 16B-aligned).
        // Reset unsupported flag and fall through to element-by-element path.
        params.para_is_supported = true;
        params.para_error_msg.clear();
    }

    // Non-contiguous (or contiguous-but-unaligned): element-by-element path.
    if (!build_noncontiguous_select_plan(params, shape, stride, dim, index,
                                         storage_offset, elem_bytes)) {
        return;
    }
    // Build LOGIC shift-amount LUT if any segments need byte repositioning.
    if (params.para_has_logic_segments) {
        build_shift_lut(params);
    }
}

SelectIntOpParams params_from_request(const app::AppOperatorRequest& req) {
    SelectIntOpParams params;
    params.para_n = req.para_dimensions.para_n;
    params.para_c = req.para_dimensions.para_c;
    params.para_h = req.para_dimensions.para_h;
    params.para_w = req.para_dimensions.para_w;

    const app::AppTensorDesc* input = find_input_tensor(req);
    if (input != nullptr) {
        params.para_dtype = input->para_dtype;
    }

    params.para_input_bank = static_cast<uint8_t>(get_i32_param(req, "input_bank", 0));
    params.para_input_addr = static_cast<uint16_t>(get_i32_param(req, "input_addr", 0));
    params.para_output_bank = static_cast<uint8_t>(get_i32_param(req, "output_bank", 2));
    params.para_output_addr = static_cast<uint16_t>(get_i32_param(req, "output_addr", 0));
    params.para_smc_id_en = static_cast<uint8_t>(get_i32_param(req, "smc_id_en", 0));
    params.para_source_offset_words = static_cast<uint32_t>(
        get_i32_param(req, "source_offset_words", get_i32_param(req, "select_offset_words", 0)));
    params.para_loop_chunk_words = static_cast<uint32_t>(
        get_i32_param(req, "loop_chunk_words", static_cast<int>(kDefaultLoopChunkWords)));
    params.para_input_bank_stride_words = static_cast<uint32_t>(
        get_i32_param(req, "input_bank_stride_words", 0));
    params.para_output_bank_stride_words = static_cast<uint32_t>(
        get_i32_param(req, "output_bank_stride_words", 0));
    params.para_use_ldb_stb =
        (req.para_layout_mode == "ldb_stb") ||
        (req.para_layout_mode == "select_int_ldb_stb") ||
        (get_i32_param(req, "use_ldb_stb", 0) != 0);
    params.para_use_ddr =
        (req.para_layout_mode == "ddr") ||
        (req.para_layout_mode == "select_int_ddr") ||
        (get_i32_param(req, "use_ddr", 0) != 0);
    if (params.para_use_ddr) {
        params.para_use_ldb_stb = false;
    }
    params.para_input_ddr_base_addr =
        get_u64_param(req, "input_ddr_base_addr", get_u64_param(req, "ddr_input_base_addr", 0));
    params.para_output_ddr_base_addr =
        get_u64_param(req, "output_ddr_base_addr", get_u64_param(req, "ddr_output_base_addr", 0));
    params.para_ddr_intlv_addr =
        get_u64_param(req, "ddr_intlv_addr", kDefaultDdrIntlvAddr);
    params.para_ddr_store_delay_insts = static_cast<uint32_t>(
        get_i32_param(req, "ddr_store_delay_insts", 0));
    if (params.para_loop_chunk_words == 0) {
        params.para_loop_chunk_words = kDefaultLoopChunkWords;
    }
    if (params.para_loop_chunk_words > kMaxLoopWords) {
        params.para_loop_chunk_words = kMaxLoopWords;
    }

    if (input == nullptr) {
        mark_unsupported(params, "SelectInt requires input_tensor/input");
        return params;
    }

    if (params.para_input_bank_stride_words == 0) {
        const uint32_t input_words = words_for_input(*input, req);
        const uint32_t first_bank_capacity =
            (params.para_input_addr <= kMaxUrAddr)
                ? static_cast<uint32_t>(kMaxUrAddr + 1 - params.para_input_addr)
                : 0;
        if (first_bank_capacity == 0) {
            mark_unsupported(params, "SelectInt input_addr is out of range");
            return params;
        }
        if (input_words > first_bank_capacity) {
            params.para_input_bank_stride_words = first_bank_capacity;
        }
    }

    if (has_i32_param(req, "dim") || has_i32_param(req, "index")) {
        build_pytorch_select_plan(params, *input, req);
    } else {
        build_legacy_contiguous_plan(params, input, req);
    }

    if (params.para_use_ldb_stb && params.para_is_supported) {
        if (params.para_has_logic_segments) {
            mark_unsupported(params, "SelectInt LDB/STB mode does not support LOGIC-shift segments");
            return params;
        }
    }

    if (params.para_use_ddr && params.para_is_supported) {
        if (params.para_has_logic_segments) {
            mark_unsupported(params, "SelectInt DDR mode does not support LOGIC-shift segments");
            return params;
        }
        for (const auto& segment : params.para_segments) {
            if (segment.para_dst_byte_enable != 0) {
                uint16_t bytes = 0;
                if (!segment_partial_byte_count(segment, bytes)) {
                    mark_unsupported(params, "SelectInt DDR partial element crosses a 16-byte boundary");
                    return params;
                }
                continue;
            }
            if ((segment.para_src_byte_offset % kUrWordBytes) != 0 ||
                (segment.para_dst_byte_offset % kUrWordBytes) != 0) {
                mark_unsupported(params, "SelectInt DDR mode requires 16-byte aligned full-word copies");
                return params;
            }
        }
    }

    return params;
}

} // namespace

std::vector<SelectIntChunkDesc> SelectIntOp::plan_contiguous_slice_chunks(
    const SelectIntContiguousSlicePlan& plan) {
    if (plan.para_max_words_per_smc == 0) {
        throw std::invalid_argument("select_int chunk plan requires max_words_per_smc > 0");
    }
    if (plan.para_num_smc == 0 || plan.para_num_smc > 8) {
        throw std::invalid_argument("select_int chunk plan requires num_smc in [1, 8]");
    }

    const uint64_t total_lines =
        (plan.para_output_total_bytes + kUrWordBytes - 1u) / kUrWordBytes;
    const uint64_t lines_per_chunk =
        static_cast<uint64_t>(plan.para_max_words_per_smc) * plan.para_num_smc;

    std::vector<SelectIntChunkDesc> chunks;
    for (uint64_t line_base = 0, idx = 0;
         line_base < total_lines;
         line_base += lines_per_chunk, ++idx) {
        SelectIntChunkDesc c;
        c.para_chunk_index = static_cast<uint32_t>(idx);
        c.para_selected_input_byte_base =
            plan.para_selected_input_byte_base + line_base * kUrWordBytes;
        c.para_output_byte_base = line_base * kUrWordBytes;

        uint16_t max_valid_words = 0;
        for (uint8_t smc = 0; smc < plan.para_num_smc; ++smc) {
            const uint64_t smc_line_base =
                line_base + static_cast<uint64_t>(smc) * plan.para_max_words_per_smc;
            uint32_t valid_bytes = 0;
            if (smc_line_base < total_lines) {
                const uint64_t byte_base = smc_line_base * kUrWordBytes;
                const uint64_t clipped_base =
                    std::min(byte_base, plan.para_output_total_bytes);
                const uint64_t remain = plan.para_output_total_bytes - clipped_base;
                valid_bytes = static_cast<uint32_t>(
                    std::min<uint64_t>(remain,
                                       static_cast<uint64_t>(plan.para_max_words_per_smc) *
                                           kUrWordBytes));
            }
            c.para_valid_bytes_per_smc[smc] = valid_bytes;
            const uint16_t valid_words =
                static_cast<uint16_t>((static_cast<uint64_t>(valid_bytes) +
                                       kUrWordBytes - 1u) /
                                      kUrWordBytes);
            max_valid_words = std::max(max_valid_words, valid_words);
        }
        c.para_words_per_smc = max_valid_words;
        chunks.push_back(c);
    }
    return chunks;
}

SelectIntOpParams SelectIntOp::params_for_chunk(const SelectIntOpParams& base,
                                                const SelectIntChunkDesc& chunk) {
    SelectIntOpParams p = base;
    p.para_n = 1;
    p.para_c = 1;
    p.para_h = 1;
    p.para_w = static_cast<uint32_t>(
        (static_cast<uint64_t>(chunk.para_words_per_smc) * kUrWordBytes) /
        bytes_per_element(p.para_dtype));
    p.para_select_words = chunk.para_words_per_smc;
    p.para_output_bytes = static_cast<uint32_t>(
        std::min<uint64_t>(static_cast<uint64_t>(chunk.para_words_per_smc) *
                               kUrWordBytes,
                           UINT32_MAX));
    p.para_input_ddr_base_addr = chunk.para_selected_input_byte_base;
    p.para_output_ddr_base_addr = chunk.para_output_byte_base;
    return p;
}

std::vector<IqInstructionFile> SelectIntOpInstructions::get_iq_files() const {
    std::vector<IqInstructionFile> files;

    if (para_params.para_use_ddr) {
        auto load_files = select_int_load(para_params);
        auto select_files = select_int_select(para_params);
        auto store_files = select_int_store(para_params);
        files.insert(files.end(),
                     std::make_move_iterator(load_files.begin()),
                     std::make_move_iterator(load_files.end()));
        files.insert(files.end(),
                     std::make_move_iterator(select_files.begin()),
                     std::make_move_iterator(select_files.end()));
        files.insert(files.end(),
                     std::make_move_iterator(store_files.begin()),
                     std::make_move_iterator(store_files.end()));
        return files;
    }

    if (para_params.para_use_ldb_stb) {
        IqInstructionFile stb_iq;
        stb_iq.para_iq_id = kV419IqIdStb;
        stb_iq.para_iq_name = iq::IQ_NAME_STB;
        stb_iq.para_width_bits = 100;

        IqInstructionFile ldb_iq;
        ldb_iq.para_iq_id = kV419IqIdLdb;
        ldb_iq.para_iq_name = iq::IQ_NAME_LDB;
        ldb_iq.para_width_bits = 106;

        for (size_t segment_idx = 0;
             segment_idx < para_params.para_segments.size();
             ++segment_idx) {
            const auto& segment = para_params.para_segments[segment_idx];
            if (segment.para_dst_byte_enable != 0) {
                uint16_t bytes = 0;
                if (!segment_partial_byte_count(segment, bytes)) {
                    continue;
                }
                const uint16_t src_start = segment_src_lsu_addr(segment);
                const uint16_t dst_start = segment_dst_lsu_addr(segment);

                const uint64_t dst_word_base =
                    segment.para_dst_byte_offset / kUrWordBytes;
                const uint8_t dst_pos = static_cast<uint8_t>(
                    segment.para_dst_byte_offset % kUrWordBytes);
                if (dst_pos == 0 && bytes > 0 && (kUrWordBytes % bytes) == 0) {
                    const uint32_t elems_per_dst_word =
                        static_cast<uint32_t>(kUrWordBytes / bytes);
                    bool can_pack_full_word =
                        segment_idx + elems_per_dst_word <= para_params.para_segments.size();
                    uint16_t src_inc = 0;
                    uint16_t prev_src = src_start;

                    for (uint32_t k = 0; can_pack_full_word && k < elems_per_dst_word; ++k) {
                        const auto& s = para_params.para_segments[segment_idx + k];
                        uint16_t s_bytes = 0;
                        const uint8_t s_dst_pos = static_cast<uint8_t>(
                            s.para_dst_byte_offset % kUrWordBytes);
                        if (!segment_partial_byte_count(s, s_bytes) ||
                            s_bytes != bytes ||
                            s.para_src_bank != segment.para_src_bank ||
                            s.para_dst_bank != segment.para_dst_bank ||
                            (s.para_dst_byte_offset / kUrWordBytes) != dst_word_base ||
                            s_dst_pos != static_cast<uint8_t>(k * bytes)) {
                            can_pack_full_word = false;
                            break;
                        }
                        const uint16_t s_src = segment_src_lsu_addr(s);
                        if (k == 1) {
                            if (s_src <= prev_src) {
                                can_pack_full_word = false;
                                break;
                            }
                            src_inc = static_cast<uint16_t>(s_src - prev_src);
                        } else if (k > 1) {
                            if (s_src <= prev_src ||
                                static_cast<uint16_t>(s_src - prev_src) != src_inc) {
                                can_pack_full_word = false;
                                break;
                            }
                        }
                        prev_src = s_src;
                    }

                    if (can_pack_full_word && (elems_per_dst_word == 1 || src_inc != 0)) {
                        const uint16_t stb_line_byte_cnt =
                            (elems_per_dst_word == 1) ? 0xFu : static_cast<uint16_t>(bytes - 1);
                        auto stb_inst = make_stb_instruction(
                            para_params.para_smc_id_en,
                            0,  // tid=0: direct STB -> LDB stream, no STBAXI.
                            src_start,
                            static_cast<uint16_t>(elems_per_dst_word - 1),
                            (elems_per_dst_word > 1) ? src_inc : 0,
                            0,
                            stb_line_byte_cnt);
                        auto ldb_inst = make_ldb_instruction(
                            para_params.para_smc_id_en,
                            dst_start,
                            0,
                            0,
                            0,
                            0xFu);
                        stb_iq.para_data.insert(stb_iq.para_data.end(),
                                                stb_inst.begin(), stb_inst.end());
                        ldb_iq.para_data.insert(ldb_iq.para_data.end(),
                                                ldb_inst.begin(), ldb_inst.end());

                        segment_idx += static_cast<size_t>(elems_per_dst_word - 1);
                        if (segment_idx + 1 < para_params.para_segments.size() &&
                            para_params.para_segments[segment_idx + 1].para_dst_byte_enable != 0) {
                            auto stb_idle = make_stb_idle_instruction();
                            for (int pad = 0; pad < 3; ++pad) {
                                stb_iq.para_data.insert(stb_iq.para_data.end(),
                                                        stb_idle.begin(), stb_idle.end());
                            }
                        }
                        continue;
                    }
                }

                const bool word_start_copy =
                    (segment.para_src_byte_offset % kUrWordBytes) == 0 &&
                    (segment.para_dst_byte_offset % kUrWordBytes) == 0;
                const uint16_t line_byte_cnt =
                    word_start_copy ? 0xFu : static_cast<uint16_t>(bytes - 1);
                // Keep partial element transfers on the single-element path.
                // The current RTL path is verified for one partial STB->LDB
                // transaction, while looped/coalesced partial transfers can
                // overrun the small LDB/STB instruction arrays and drop writes.
                constexpr uint8_t kPartialLsuDly = 7;
                const uint8_t partial_dly = word_start_copy ? 0u : kPartialLsuDly;
                auto stb_inst = make_stb_instruction(
                    para_params.para_smc_id_en,
                    0,  // tid=0: direct STB -> LDB stream, no STBAXI.
                    src_start,
                    0,
                    0,
                    partial_dly,
                    line_byte_cnt);
                auto ldb_inst = make_ldb_instruction(
                    para_params.para_smc_id_en,
                    dst_start,
                    0,
                    0,
                    partial_dly,
                    line_byte_cnt);
                stb_iq.para_data.insert(stb_iq.para_data.end(), stb_inst.begin(), stb_inst.end());
                ldb_iq.para_data.insert(ldb_iq.para_data.end(), ldb_inst.begin(), ldb_inst.end());
                if (segment_idx + 1 < para_params.para_segments.size() &&
                    para_params.para_segments[segment_idx + 1].para_dst_byte_enable != 0) {
                    auto stb_idle = make_stb_idle_instruction();
                    auto ldb_idle = make_ldb_idle_instruction();
                    for (int pad = 0; pad < 3; ++pad) {
                        stb_iq.para_data.insert(stb_iq.para_data.end(),
                                                stb_idle.begin(), stb_idle.end());
                        ldb_iq.para_data.insert(ldb_iq.para_data.end(),
                                                ldb_idle.begin(), ldb_idle.end());
                    }
                }
                continue;
            }

            uint32_t emitted = 0;
            while (emitted < segment.para_words) {
                const uint32_t remaining = segment.para_words - emitted;
                const uint32_t chunk_words = std::min(remaining, para_params.para_loop_chunk_words);
                const uint16_t loops = static_cast<uint16_t>(chunk_words - 1);
                const uint16_t inc = (chunk_words > 1) ? 0x10u : 0u;
                const uint16_t src_addr = static_cast<uint16_t>(segment.para_src_addr + emitted);
                const uint16_t dst_addr = static_cast<uint16_t>(segment.para_dst_addr + emitted);

                auto stb_inst = make_stb_instruction(
                    para_params.para_smc_id_en,
                    0,  // tid=0: direct STB -> LDB stream, no STBAXI.
                    encode_lsu_ur_addr(segment.para_src_bank, src_addr),
                    loops,
                    inc);
                auto ldb_inst = make_ldb_instruction(
                    para_params.para_smc_id_en,
                    encode_lsu_ur_addr(segment.para_dst_bank, dst_addr),
                    loops,
                    inc);

                stb_iq.para_data.insert(stb_iq.para_data.end(), stb_inst.begin(), stb_inst.end());
                ldb_iq.para_data.insert(ldb_iq.para_data.end(), ldb_inst.begin(), ldb_inst.end());
                emitted += chunk_words;
            }
        }

        auto stb_end = make_stb_end_instruction();
        auto ldb_end = make_ldb_end_instruction();
        stb_iq.para_data.insert(stb_iq.para_data.end(), stb_end.begin(), stb_end.end());
        ldb_iq.para_data.insert(ldb_iq.para_data.end(), ldb_end.begin(), ldb_end.end());

        files.push_back(std::move(stb_iq));
        files.push_back(std::move(ldb_iq));
        return files;
    }

    const bool use_logic = para_params.para_has_logic_segments;
    const size_t elem_bytes = bytes_per_element(para_params.para_dtype);
    const bool is_32bit = (elem_bytes == 4);

    // ---- UR read port 0 ----
    IqInstructionFile ur_r0;
    ur_r0.para_iq_id = iq::IQ_ID_UR_RDATA_0;
    ur_r0.para_iq_name = iq::IQ_NAME_UR_RDATA_0;
    ur_r0.para_width_bits = 61;

    // ---- RtoVR route 1: UR_RDATA_0 → DVR_LOGIC_S0 (when LOGIC used) or UVR_WDATA_0 ----
    IqInstructionFile rtovr_route1;
    rtovr_route1.para_iq_id = iq::IQ_ID_RTOVR_BASE;
    rtovr_route1.para_width_bits = 48;

    // ---- LOGIC (only when shifts are needed) ----
    IqInstructionFile logic_iq;
    if (use_logic) {
        logic_iq.para_iq_id = iq::IQ_ID_LOGIC;
        logic_iq.para_iq_name = iq::IQ_NAME_LOGIC;
        logic_iq.para_width_bits = 47;  // 41 header + 6 code
    }

    // ---- RtoVR route 2: DR_LOGIC_D → UVR_WDATA_0 (when LOGIC used) ----
    IqInstructionFile rtovr_route2;
    if (use_logic) {
        rtovr_route2.para_iq_id = static_cast<uint32_t>(iq::IQ_ID_RTOVR_BASE) + 1;
        rtovr_route2.para_width_bits = 48;
    }

    // ---- RtoVR route 3: UR_RDATA_1 → DVR_LOGIC_S1 (shift amount, when LOGIC used) ----
    IqInstructionFile rtovr_route3;
    if (use_logic) {
        rtovr_route3.para_iq_id = static_cast<uint32_t>(iq::IQ_ID_RTOVR_BASE) + 2;
        rtovr_route3.para_width_bits = 48;
    }

    // ---- UR read port 1 (shift-amount LUT, when LOGIC used) ----
    IqInstructionFile ur_r1;
    if (use_logic) {
        ur_r1.para_iq_id = iq::IQ_ID_UR_RDATA_1;
        ur_r1.para_iq_name = iq::IQ_NAME_UR_RDATA_1;
        ur_r1.para_width_bits = 61;
    }

    // ---- UR write port 0 ----
    IqInstructionFile ur_w0;
    ur_w0.para_iq_id = iq::IQ_ID_UR_WDATA_0;
    ur_w0.para_iq_name = iq::IQ_NAME_UR_WDATA_0;
    ur_w0.para_width_bits = 74;

    if (use_logic) {
        rtovr_route1.para_iq_name = "rtovr-" + std::to_string(kRtovrOutputDvrLogicS0);
        rtovr_route2.para_iq_name = "rtovr-" + std::to_string(kRtovrOutputUvrWdata0);
        rtovr_route3.para_iq_name = "rtovr-" + std::to_string(kRtovrOutputDvrLogicS1);
    } else {
        rtovr_route1.para_iq_name = "rtovr-" + std::to_string(kRtovrOutputUvrWdata0);
    }

    const uint8_t rtovr_code_direct =
        make_rtovr_port_code(kRtovrInputUrRdata0);  // UR_RDATA_0 → output
    const uint8_t rtovr_code_to_logic_s0 =
        make_rtovr_port_code(kRtovrInputUrRdata0);  // UR_RDATA_0 → DVR_LOGIC_S0
    const uint8_t rtovr_code_to_logic_s1 =
        make_rtovr_port_code(kRtovrInputUrRdata1);  // UR_RDATA_1 → DVR_LOGIC_S1
    const uint8_t rtovr_code_from_logic =
        make_rtovr_port_code(kRtovrInputDrLogicD);  // DR_LOGIC_D → UVR_WDATA_0

    // LOGIC COPY NOP (for segments without shift when logic is enabled)
    const uint8_t logic_nop_code = encode_logic_code(kLogicOpCopy, is_32bit);

    bool compensate_next_read_inc = false;

    for (size_t segment_idx = 0; segment_idx < para_params.para_segments.size(); ++segment_idx) {
        const auto& segment = para_params.para_segments[segment_idx];
        uint32_t emitted = 0;
        while (emitted < segment.para_words) {
        const uint32_t remaining = segment.para_words - emitted;
        const uint32_t chunk_words = std::min(remaining, para_params.para_loop_chunk_words);
        const uint8_t inc = (chunk_words > 1) ? 1u : 0u;

        uint16_t src_addr = static_cast<uint16_t>(segment.para_src_addr + emitted);
        const uint16_t dst_addr = static_cast<uint16_t>(segment.para_dst_addr + emitted);
        if (compensate_next_read_inc && src_addr > 0) {
            --src_addr;
        }
        const uint32_t read_code = encode_ur_read_code(segment.para_src_bank, src_addr,
                                                       para_params.para_smc_id_en);

        // Write code: use byte-enable when set, otherwise full-word write.
        uint64_t write_code;
        if (segment.para_dst_byte_enable != 0 && segment.para_words == 1) {
            write_code = encode_ur_write_code(segment.para_dst_bank, dst_addr,
                                              segment.para_dst_byte_enable,
                                              para_params.para_smc_id_en);
        } else {
            write_code = encode_ur_write_code(segment.para_dst_bank, dst_addr,
                                              0xFFFFu, para_params.para_smc_id_en);
        }

        const bool use_loop = chunk_words > 1;
        const uint16_t loops = static_cast<uint16_t>(chunk_words - 1);
        const bool extend_compensated_read = compensate_next_read_inc && use_loop;
        const uint16_t read_loops = static_cast<uint16_t>(loops + (extend_compensated_read ? 1u : 0u));
        const uint16_t rtovr_loops = read_loops;
        const uint16_t write_loops = loops;
        const uint8_t rtovr_dly = use_loop ? 0u : 1u;
        const uint8_t write_dly_base = use_loop ? 1u : 2u;

        // ---- UR read instruction ----
        auto read_inst = make_small_code_instruction(1, 0, read_loops, 0, inc, read_code, 20);

        if (use_logic && segment.para_needs_logic_shift) {
            // ---- LOGIC-based byte repositioning path ----
            // Pipeline: UR read → RtoVR(to LOGIC S0/S1) → LOGIC shift → RtoVR(to UR WDATA) → UR write
            // DLY offsets are cumulative:
            //   read (t=0) → rtovr1 (t=1) → logic (t=2) → rtovr2 (t=3) → write (t=4)

            // RtoVR route 1: UR_RDATA_0 → DVR_LOGIC_S0
            auto rtovr1_inst = make_small_code_instruction(1, 0, rtovr_loops, 1, 0,
                                                           rtovr_code_to_logic_s0, 7);

            // RtoVR route 3: UR_RDATA_1 → DVR_LOGIC_S1 (shift amount)
            auto rtovr3_inst = make_small_code_instruction(1, 0, rtovr_loops, 1, 0,
                                                           rtovr_code_to_logic_s1, 7);

            // LOGIC shift: encode op_type + precision
            const uint8_t logic_op = segment.para_shift_right
                                         ? kLogicOpLogicRightShift
                                         : kLogicOpLogicLeftShift;
            const uint8_t logic_code = encode_logic_code(logic_op, is_32bit);
            auto logic_inst = make_small_code_instruction(1, 0, write_loops, 1, 0,
                                                          logic_code, 6);

            // RtoVR route 2: DR_LOGIC_D → UVR_WDATA_0
            auto rtovr2_inst = make_small_code_instruction(1, 0, write_loops, 1, 0,
                                                           rtovr_code_from_logic, 7);

            // UR write
            const uint8_t write_dly = 1;
            auto write_inst = make_ur_wdata_instruction(1, 0, write_loops, write_dly, inc,
                                                        write_code);

            // UR read port 1: shift-amount LUT (same address, no increment)
            const uint32_t s1_read_code = encode_ur_read_code(segment.para_logic_s1_bank,
                                                              segment.para_logic_s1_addr,
                                                              para_params.para_smc_id_en);
            auto s1_read_inst = make_small_code_instruction(1, 0, 0, 0, 0, s1_read_code, 20);

            ur_r0.para_data.insert(ur_r0.para_data.end(), read_inst.begin(), read_inst.end());
            rtovr_route1.para_data.insert(rtovr_route1.para_data.end(), rtovr1_inst.begin(), rtovr1_inst.end());
            rtovr_route3.para_data.insert(rtovr_route3.para_data.end(), rtovr3_inst.begin(), rtovr3_inst.end());
            logic_iq.para_data.insert(logic_iq.para_data.end(), logic_inst.begin(), logic_inst.end());
            rtovr_route2.para_data.insert(rtovr_route2.para_data.end(), rtovr2_inst.begin(), rtovr2_inst.end());
            ur_w0.para_data.insert(ur_w0.para_data.end(), write_inst.begin(), write_inst.end());
            ur_r1.para_data.insert(ur_r1.para_data.end(), s1_read_inst.begin(), s1_read_inst.end());
        } else {
            // ---- Direct RtoVR path (original behavior) ----
            const uint8_t rtovr_code = use_logic ? rtovr_code_to_logic_s0 : rtovr_code_direct;
            auto rtovr_inst = make_small_code_instruction(1, 0, rtovr_loops, rtovr_dly, 0,
                                                          rtovr_code, 7);
            const uint8_t write_dly = use_logic ? 1u : write_dly_base;
            auto write_inst = make_ur_wdata_instruction(1, 0, write_loops, write_dly, inc,
                                                        write_code);

            ur_r0.para_data.insert(ur_r0.para_data.end(), read_inst.begin(), read_inst.end());
            rtovr_route1.para_data.insert(rtovr_route1.para_data.end(), rtovr_inst.begin(), rtovr_inst.end());
            ur_w0.para_data.insert(ur_w0.para_data.end(), write_inst.begin(), write_inst.end());

            if (use_logic) {
                // Non-shifted segments: route data through LOGIC COPY (preserves data as-is)
                auto logic_copy_inst = make_small_code_instruction(1, 0, write_loops, 1, 0,
                                                                    logic_nop_code, 6);

                // RtoVR route 2: DR_LOGIC_D → UVR_WDATA_0
                auto rtovr2_inst = make_small_code_instruction(1, 0, write_loops, 1, 0,
                                                                rtovr_code_from_logic, 7);

                // RtoVR route 3: UR_RDATA_1 → DVR_LOGIC_S1 (S1=0 for copy, data read anyway)
                auto rtovr3_copy = make_small_code_instruction(1, 0, write_loops, 1, 0,
                                                               rtovr_code_to_logic_s1, 7);

                // UR read port 1 NOP
                auto s1_nop = make_small_code_instruction(1, 0, 0, 0, 0, 0, 20);

                logic_iq.para_data.insert(logic_iq.para_data.end(), logic_copy_inst.begin(), logic_copy_inst.end());
                rtovr_route2.para_data.insert(rtovr_route2.para_data.end(), rtovr2_inst.begin(), rtovr2_inst.end());
                rtovr_route3.para_data.insert(rtovr_route3.para_data.end(), rtovr3_copy.begin(), rtovr3_copy.end());
                ur_r1.para_data.insert(ur_r1.para_data.end(), s1_nop.begin(), s1_nop.end());
            }
        }

        emitted += chunk_words;
        compensate_next_read_inc = (inc != 0);
        }
        {
            // NOP between segments (RtoVR: vld=0 to avoid overwriting mv_out config)
            auto read_nop = make_small_code_instruction(1, 0, 0, 0, 0, 0, 20);
            auto rtovr_nop = make_small_code_instruction(0, 0, 0, 0, 0, 0, 7);
            auto write_nop = make_ur_wdata_instruction(1, 0, 0, 0, 0, 0);
            ur_r0.para_data.insert(ur_r0.para_data.end(), read_nop.begin(), read_nop.end());
            rtovr_route1.para_data.insert(rtovr_route1.para_data.end(), rtovr_nop.begin(), rtovr_nop.end());
            ur_w0.para_data.insert(ur_w0.para_data.end(), write_nop.begin(), write_nop.end());

            if (use_logic) {
                auto logic_nop = make_small_code_instruction(1, 0, 0, 0, 0, 0, 6);
                auto rtovr2_nop = make_small_code_instruction(0, 0, 0, 0, 0, 0, 7);
                auto rtovr3_nop = make_small_code_instruction(0, 0, 0, 0, 0, 0, 7);
                auto s1_nop = make_small_code_instruction(1, 0, 0, 0, 0, 0, 20);
                logic_iq.para_data.insert(logic_iq.para_data.end(), logic_nop.begin(), logic_nop.end());
                rtovr_route2.para_data.insert(rtovr_route2.para_data.end(), rtovr2_nop.begin(), rtovr2_nop.end());
                rtovr_route3.para_data.insert(rtovr_route3.para_data.end(), rtovr3_nop.begin(), rtovr3_nop.end());
                ur_r1.para_data.insert(ur_r1.para_data.end(), s1_nop.begin(), s1_nop.end());
            }
        }
    }

    // ---- End instructions ----
    {
        auto end = make_small_code_instruction(1, 1, 0, 0, 0, 0, 20);
        ur_r0.para_data.insert(ur_r0.para_data.end(), end.begin(), end.end());
    }
    {
        auto end = make_small_code_instruction(0, 1, 0, 0, 0, 0, 7);
        rtovr_route1.para_data.insert(rtovr_route1.para_data.end(), end.begin(), end.end());
    }
    {
        auto end = make_ur_wdata_instruction(1, 1, 0, 0, 0, 0);
        ur_w0.para_data.insert(ur_w0.para_data.end(), end.begin(), end.end());
    }
    if (use_logic) {
        {
            auto end = make_small_code_instruction(1, 1, 0, 0, 0, 0, 6);
            logic_iq.para_data.insert(logic_iq.para_data.end(), end.begin(), end.end());
        }
        {
            auto end = make_small_code_instruction(0, 1, 0, 0, 0, 0, 7);
            rtovr_route2.para_data.insert(rtovr_route2.para_data.end(), end.begin(), end.end());
        }
        {
            auto end = make_small_code_instruction(0, 1, 0, 0, 0, 0, 7);
            rtovr_route3.para_data.insert(rtovr_route3.para_data.end(), end.begin(), end.end());
        }
        {
            auto end = make_small_code_instruction(1, 1, 0, 0, 0, 0, 20);
            ur_r1.para_data.insert(ur_r1.para_data.end(), end.begin(), end.end());
        }
    }

    files.push_back(std::move(ur_r0));
    files.push_back(std::move(rtovr_route1));
    if (use_logic) {
        files.push_back(std::move(rtovr_route3));
        files.push_back(std::move(logic_iq));
        files.push_back(std::move(rtovr_route2));
        files.push_back(std::move(ur_r1));
    }
    files.push_back(std::move(ur_w0));
    return files;
}

std::vector<uint8_t> SelectIntOpInstructions::serialize() const {
    std::vector<uint8_t> out;
    for (const auto& f : get_iq_files()) {
        out.insert(out.end(), f.para_data.begin(), f.para_data.end());
    }
    return out;
}

std::string SelectIntOp::get_name() const {
    return "SelectInt";
}

SelectIntOpInstructions SelectIntOp::compile(const SelectIntOpParams& params) {
    SelectIntOpInstructions insts;
    insts.para_params = params;
    insts.para_total_steps = params.para_select_words;
    insts.para_total_elements = params.para_output_bytes /
                                static_cast<uint32_t>(bytes_per_element(params.para_dtype));
    return insts;
}

OpInstructionsBase* SelectIntOp::compile(const app::AppOperatorRequest& req) {
    SelectIntOpParams params = params_from_request(req);
    if (!params.para_is_supported) {
        std::cerr << "[SelectInt] Unsupported request: " << params.para_error_msg << "\n";
        return nullptr;
    }
    return new SelectIntOpInstructions(compile(params));
}

bool SelectIntOp::calculate_golden(const app::AppOperatorRequest& req,
                                   std::vector<uint8_t>& golden_bin) {
    golden_bin.clear();
    const app::AppTensorDesc* input = find_input_tensor(req);
    if (input == nullptr) return false;

    const SelectIntOpParams params = params_from_request(req);
    if (!params.para_is_supported) {
        std::cerr << "[SelectInt] Unsupported golden request: " << params.para_error_msg << "\n";
        return false;
    }
    const std::vector<uint8_t> input_bin = serialize_tensor(*input);
    const size_t elem_bytes = bytes_per_element(params.para_dtype);
    golden_bin.assign(params.para_output_bytes, 0);
    for (const auto& segment : params.para_segments) {
        const size_t src_off = segment.para_src_byte_offset;
        const size_t dst_off = segment.para_dst_byte_offset;

        if (segment.para_dst_byte_enable != 0) {
            // Element-level copy: the segment represents one element.
            // Copy elem_bytes from source to destination regardless of
            // LOGIC shifting (which is a hardware pipeline concern).
            if (src_off + elem_bytes <= input_bin.size() &&
                dst_off + elem_bytes <= golden_bin.size()) {
                std::memcpy(golden_bin.data() + dst_off,
                            input_bin.data() + src_off,
                            elem_bytes);
            }
        } else {
            // Full-word copy.
            const size_t copy_bytes = static_cast<size_t>(segment.para_words) * kUrWordBytes;
            if (src_off >= input_bin.size() || dst_off >= golden_bin.size()) continue;
            const size_t src_available = input_bin.size() - src_off;
            const size_t dst_available = golden_bin.size() - dst_off;
            const size_t n = std::min(copy_bytes, std::min(src_available, dst_available));
            std::memcpy(golden_bin.data() + dst_off, input_bin.data() + src_off, n);
        }
    }
    return true;
}

bool SelectIntOp::generate_data(const app::AppOperatorRequest& req,
                                std::vector<uint8_t>& input_bin,
                                std::vector<uint8_t>& other_bin,
                                std::vector<uint8_t>& alpha_bin) {
    input_bin.clear();
    other_bin.clear();
    alpha_bin.clear();

    const app::AppTensorDesc* input = find_input_tensor(req);
    if (input == nullptr) return false;
    input_bin = serialize_tensor(*input);

    // If the plan uses LOGIC, also emit the shift-amount LUT into other_bin.
    SelectIntOpParams params = params_from_request(req);
    if (params.para_is_supported && params.para_has_logic_segments &&
        !params.para_shift_lut_data.empty()) {
        other_bin = params.para_shift_lut_data;
    }

    return true;
}

void SelectIntOp::fill_random_request(app::AppOperatorRequest& req,
                                      std::mt19937& rng,
                                      std::uniform_real_distribution<float>& data_dist,
                                      const std::string& dtype_str) {
    req.para_operator = "SelectInt";
    req.para_layout_mode = "default";

    const app::AppDataType dtype = (dtype_str == "int32") ? app::AppDataType::INT32 :
                                   (dtype_str == "fp16") ? app::AppDataType::FLOAT16 :
                                                           app::AppDataType::FLOAT32;
    req.para_dimensions.para_n = 2;
    req.para_dimensions.para_c = 1;
    req.para_dimensions.para_h = 3;
    req.para_dimensions.para_w = (dtype == app::AppDataType::FLOAT16) ? 16 : 8;

    const uint32_t total = req.para_dimensions.para_n * req.para_dimensions.para_c *
                           req.para_dimensions.para_h * req.para_dimensions.para_w;

    app::AppTensorDesc input;
    input.para_name = "input_tensor";
    input.para_dtype = dtype;
    input.para_shape = {req.para_dimensions.para_n, req.para_dimensions.para_c,
                        req.para_dimensions.para_h, req.para_dimensions.para_w};

    if (dtype == app::AppDataType::INT32) {
        std::uniform_int_distribution<int32_t> int_dist(-1024, 1024);
        for (uint32_t i = 0; i < total; ++i) input.para_data_i32.push_back(int_dist(rng));
    } else {
        for (uint32_t i = 0; i < total; ++i) input.para_data_f32.push_back(data_dist(rng));
    }
    req.para_inputs["input_tensor"] = input;

    auto add_i32_param = [&](const std::string& name, int value) {
        app::AppTensorDesc p;
        p.para_name = name;
        p.para_dtype = app::AppDataType::INT32;
        p.para_shape = {1};
        p.para_data_i32.push_back(value);
        req.para_parameters[name] = p;
    };

    // Add dim and index for PyTorch-compatible select.int semantics.
    // Pick dim from [0,1,2] (avoid dim=3 for now since innermost-dim blocks
    // are not 16B-aligned and element-by-element path requires src_pos==dst_pos).
    std::uniform_int_distribution<int> dim_dist(0, 2);
    const int dim = dim_dist(rng);
    const int dim_size = (dim == 0) ? req.para_dimensions.para_n :
                         (dim == 1) ? req.para_dimensions.para_c :
                                      req.para_dimensions.para_h;
    std::uniform_int_distribution<int> idx_dist(0, dim_size - 1);
    const int index = idx_dist(rng);

    add_i32_param("dim", dim);
    add_i32_param("index", index);

    add_i32_param("input_bank", 0);
    add_i32_param("input_addr", 0);
    add_i32_param("output_bank", 2);
    add_i32_param("output_addr", 0);
    add_i32_param("source_offset_words", 0);
    add_i32_param("select_words", 1);
    add_i32_param("loop_chunk_words", static_cast<int>(kDefaultLoopChunkWords));
}

REGISTER_OP(SelectIntOp);

} // namespace op
} // namespace aiacc
