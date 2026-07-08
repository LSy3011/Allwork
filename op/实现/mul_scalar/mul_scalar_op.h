#ifndef MUL_SCALAR_OP_H
#define MUL_SCALAR_OP_H

#include <array>
#include <cstdint>
#include <random>
#include <string>
#include <utility>
#include <vector>

#include "../../app/app_json_parser.h"
#include "../../hal/aiacc_smi_iq.h"
#include "../../iq/modules/fpmul_iq.h"
#include "../../iq/modules/rtovr_iq.h"
#include "../../iq/modules/ur_iq.h"
#include "../common/op_base.h"
#include "../common/op_instructions_base.h"
#include "../common/op_registry.h"

namespace aiacc {
namespace op {

enum class MulScalarDataType : uint8_t {
    FP32 = 0,
    FP16 = 1
};

struct MulScalarChunkDesc {
    uint32_t para_chunk_index{0};
    uint64_t para_global_line_base{0};
    uint16_t para_words_per_smc{0};
    std::array<uint16_t, 8> para_valid_words_per_smc{};
};

struct MulScalarChunkPlan {
    uint64_t para_total_bytes{0};
    uint16_t para_max_words_per_smc{255};
    uint8_t para_num_smc{8};
};

struct MulScalarOpParams {
    uint32_t para_n{1};
    uint32_t para_c{1};
    uint32_t para_h{1};
    uint32_t para_w{4};

    MulScalarDataType para_dtype{MulScalarDataType::FP32};

    uint8_t para_input_bank{0};
    uint8_t para_input_addr{0};

    uint8_t para_other_bank{1};
    uint8_t para_other_addr{0};

    uint8_t para_output_bank{2};
    uint8_t para_output_addr{0};
    uint8_t para_smc_id_en{0};

    uint64_t para_input_ddr_base_addr{0};
    uint64_t para_other_ddr_base_addr{0x100000ULL};
    uint64_t para_output_ddr_base_addr{0x200000ULL};
    uint64_t para_ddr_intlv_addr{0x10000000ULL};
    uint32_t para_compute_start_delay_insts{0};
    uint32_t para_store_start_delay_insts{0};
    bool para_identity_scalar{false};

    uint32_t get_elems_per_word() const {
        return (para_dtype == MulScalarDataType::FP16) ? 8u : 4u;
    }
};

struct MulScalarOpInstructions : public OpInstructionsBase {
    std::vector<iq::UrCru> para_ur_read_crus;
    std::vector<iq::UrCru> para_ur_write_crus;

    // (target_port, src_id)
    std::vector<std::pair<uint8_t, uint8_t>> para_rtovr_routes;

    uint8_t para_fpmul_inst{0};
    MulScalarDataType para_dtype{MulScalarDataType::FP32};
    uint8_t para_smc_id_en{0};

    uint32_t para_total_steps{0};
    uint32_t para_total_elements{0};
    uint32_t para_total_cycles{0};
    MulScalarOpParams para_params;

    std::vector<IqInstructionFile> get_iq_files() const override;
    std::string get_operator_name() const override { return "MulScalar"; }
    uint32_t get_total_steps() const override { return para_total_steps; }
    uint32_t get_total_elements() const override { return para_total_elements; }
    std::vector<uint8_t> serialize() const override;
};

class MulScalarOp : public OpBase {
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

    MulScalarOpInstructions compile(const MulScalarOpParams& params);
    static std::vector<MulScalarChunkDesc> plan_full_shape_chunks(
        const MulScalarChunkPlan& plan);
    static MulScalarOpParams params_for_chunk(const MulScalarOpParams& base,
                                              const MulScalarChunkDesc& chunk);
};

void print_mul_scalar_instructions(const MulScalarOpInstructions& insts);

} // namespace op
} // namespace aiacc

#endif // MUL_SCALAR_OP_H
