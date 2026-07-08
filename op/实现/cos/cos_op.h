#ifndef AIACC_SMI_IQ_OP_COS_OP_H_
#define AIACC_SMI_IQ_OP_COS_OP_H_

#include <cstdint>
#include <random>
#include <string>
#include <vector>

#include "../../app/app_json_parser.h"
#include "../common/op_base.h"
#include "../common/op_instructions_base.h"

namespace aiacc {
namespace op {

struct CosOpParams {
    uint32_t para_n{1};
    uint32_t para_c{1};
    uint32_t para_h{1};
    uint32_t para_w{4};
    uint8_t para_dtype{0};  // 0=FP32, 1=FP16

    uint8_t para_input_bank{0};
    uint8_t para_const_bank{1};
    uint8_t para_output_bank{3};
    uint32_t para_input_addr{0};
    uint32_t para_const_addr{0};
    uint32_t para_output_addr{0};

    uint32_t para_taylor_terms{9};  // c0..c8 => expand through x^16

    uint32_t get_elems_per_word() const {
        return (para_dtype == 0) ? 4u : 8u;
    }
};

struct CosSchedEntry {
    uint32_t abs_cycle{0};
    uint32_t code{0};
};

struct CosOpInstructions : public OpInstructionsBase {
    std::vector<CosSchedEntry> sched_ur_r0;     // input x
    std::vector<CosSchedEntry> sched_ur_r1;     // scratch x2/acc
    std::vector<CosSchedEntry> sched_ur_r2;     // Taylor coefficients
    std::vector<CosSchedEntry> sched_ur_w0;     // scratch/output
    std::vector<CosSchedEntry> sched_fpmul;
    std::vector<CosSchedEntry> sched_fpadd;
    std::vector<CosSchedEntry> sched_rtovr_3;   // FPMUL S0
    std::vector<CosSchedEntry> sched_rtovr_4;   // FPMUL S1
    std::vector<CosSchedEntry> sched_rtovr_5;   // FPADD S0
    std::vector<CosSchedEntry> sched_rtovr_6;   // FPADD S1
    std::vector<CosSchedEntry> sched_rtovr_35;  // UR WDATA0

    uint8_t para_fpmul_code{0};
    uint8_t para_fpadd_code{0};
    uint32_t para_total_steps{0};
    uint32_t para_total_elements{0};
    uint32_t para_total_cycles{0};
    uint32_t para_block_cycles{0};
    uint32_t para_taylor_terms{9};

    std::string get_operator_name() const override { return "cos"; }
    uint32_t get_total_steps() const override { return para_total_steps; }
    uint32_t get_total_elements() const override { return para_total_elements; }
    std::vector<uint8_t> serialize() const override;
    std::vector<IqInstructionFile> get_iq_files() const override;
};

class CosOp : public OpBase {
public:
    std::string get_name() const override;
    OpInstructionsBase* compile(const app::AppOperatorRequest& req) override;
    bool generate_data(const app::AppOperatorRequest& req,
                       std::vector<uint8_t>& input_bin,
                       std::vector<uint8_t>& other_bin,
                       std::vector<uint8_t>& alpha_bin) override;
    bool calculate_golden(const app::AppOperatorRequest& req,
                          std::vector<uint8_t>& golden_bin) override;
    void fill_random_request(app::AppOperatorRequest& req,
                             std::mt19937& rng,
                             std::uniform_real_distribution<float>& data_dist,
                             const std::string& dtype_str) override;

private:
    CosOpParams parse_request(const app::AppOperatorRequest& req) const;
    CosOpInstructions compile(const CosOpParams& params) const;
};

} // namespace op
} // namespace aiacc

#endif // AIACC_SMI_IQ_OP_COS_OP_H_
