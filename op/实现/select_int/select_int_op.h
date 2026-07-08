#ifndef SELECT_INT_OP_H
#define SELECT_INT_OP_H

#include <array>
#include <cstdint>
#include <cstddef>
#include <random>
#include <string>
#include <vector>

#include "../../app/app_json_parser.h"
#include "../common/op_base.h"
#include "../common/op_instructions_base.h"
#include "../common/op_registry.h"

namespace aiacc {
namespace op {

struct SelectIntChunkDesc {
    uint32_t para_chunk_index{0};
    uint64_t para_selected_input_byte_base{0};
    uint64_t para_output_byte_base{0};
    uint16_t para_words_per_smc{0};
    std::array<uint32_t, 8> para_valid_bytes_per_smc{};
};

struct SelectIntContiguousSlicePlan {
    uint64_t para_selected_input_byte_base{0};
    uint64_t para_output_total_bytes{0};
    uint16_t para_max_words_per_smc{255};
    uint8_t para_num_smc{8};
};

struct SelectIntCopySegment {
    uint8_t para_src_bank{0};
    uint8_t para_dst_bank{0};
    uint16_t para_src_addr{0};
    uint16_t para_dst_addr{0};
    uint32_t para_words{0};
    size_t para_src_byte_offset{0};
    size_t para_dst_byte_offset{0};

    // Per-element byte-enable support for non-contiguous inner dimensions.
    // When non-zero, the segment copies a single 16-byte word and applies
    // this 16-bit mask (ur_wbe_0) on the write side so only the selected
    // bytes are updated.  para_dst_byte_enable == 0 means full-word copy.
    uint16_t para_dst_byte_enable{0};

    // LOGIC shift parameters for byte repositioning within a word.
    // When the source element's byte position differs from the destination
    // position, LOGIC logic_left_shift / logic_right_shift repositions the
    // data before the write.
    //   shift_right : 1 = right shift,  0 = left shift
    //   shift_bytes : number of bytes to shift (0..15)
    bool     para_needs_logic_shift{false};
    bool     para_shift_right{false};
    uint8_t  para_shift_bytes{0};
    uint32_t para_logic_s1_bank{0};     // UR bank holding shift-amount word
    uint16_t para_logic_s1_addr{0};     // UR addr holding shift-amount word
};

struct SelectIntOpParams {
    uint32_t para_n{1};
    uint32_t para_c{1};
    uint32_t para_h{1};
    uint32_t para_w{4};
    app::AppDataType para_dtype{app::AppDataType::FLOAT32};

    uint8_t para_input_bank{0};
    uint16_t para_input_addr{0};
    uint8_t para_output_bank{2};
    uint16_t para_output_addr{0};
    uint8_t para_smc_id_en{0};

    uint32_t para_source_offset_words{0};
    uint32_t para_select_words{0};
    uint32_t para_loop_chunk_words{256};
    uint32_t para_input_bank_stride_words{0};
    uint32_t para_output_bank_stride_words{0};
    uint32_t para_output_bytes{0};
    bool para_is_supported{true};
    std::string para_error_msg;
    std::vector<SelectIntCopySegment> para_segments;

    // When true the operator uses the LOGIC unit to reposition bytes within
    // a word.  This requires additional IQ files (LOGIC + extra RtoVR).
    bool para_has_logic_segments{false};
    bool para_use_ldb_stb{false};
    bool para_use_ddr{false};

    // DDR mode: LDBAXI loads selected data from external memory into UR,
    // compute is the select/no-op placement in UR, then STBAXI stores it back.
    uint64_t para_input_ddr_base_addr{0};
    uint64_t para_output_ddr_base_addr{0};
    uint64_t para_ddr_intlv_addr{0x10000000ULL};
    uint32_t para_ddr_store_delay_insts{0};

    // Pre-loaded shift-amount table: one 16-byte word per unique shift amount.
    // Indexed by (shift_right, shift_bytes).
    std::vector<uint8_t> para_shift_lut_data;
    uint8_t  para_shift_lut_bank{0};
    uint16_t para_shift_lut_addr{0};
};

struct SelectIntOpInstructions : public OpInstructionsBase {
    SelectIntOpParams para_params;
    uint32_t para_total_steps{0};
    uint32_t para_total_elements{0};

    std::vector<IqInstructionFile> get_iq_files() const override;
    std::string get_operator_name() const override { return "SelectInt"; }
    uint32_t get_total_steps() const override { return para_total_steps; }
    uint32_t get_total_elements() const override { return para_total_elements; }
    std::vector<uint8_t> serialize() const override;
};

class SelectIntOp : public OpBase {
public:
    std::string get_name() const override;
    OpInstructionsBase* compile(const app::AppOperatorRequest& req) override;
    bool calculate_golden(const app::AppOperatorRequest& req,
                          std::vector<uint8_t>& golden_bin) override;
    bool generate_data(const app::AppOperatorRequest& req,
                       std::vector<uint8_t>& input_bin,
                       std::vector<uint8_t>& other_bin,
                       std::vector<uint8_t>& alpha_bin) override;
    void fill_random_request(app::AppOperatorRequest& req,
                             std::mt19937& rng,
                             std::uniform_real_distribution<float>& data_dist,
                             const std::string& dtype_str) override;

    SelectIntOpInstructions compile(const SelectIntOpParams& params);
    static std::vector<SelectIntChunkDesc> plan_contiguous_slice_chunks(
        const SelectIntContiguousSlicePlan& plan);
    static SelectIntOpParams params_for_chunk(const SelectIntOpParams& base,
                                              const SelectIntChunkDesc& chunk);
};

} // namespace op
} // namespace aiacc

#endif // SELECT_INT_OP_H
