#ifndef NEG_OP_H
#define NEG_OP_H

/**
 * @file neg_op.h
 * @brief Neg 算子：out = -input (FP32/FP16)
 *
 * 参考 rsqrt 和 div 的 schedule-based 架构实现，确保通过 RTL 验证。
 */

#include <cstdint>
#include <vector>
#include <string>
#include <random>

#include "../../iq/modules/ur_iq.h"
#include "../../iq/modules/fpadd_iq.h"
#include "../common/op_instructions_base.h"
#include "../common/op_base.h"
#include "../common/op_registry.h"
#include "../../app/app_json_parser.h"
#include "../../app/fp16_utils.h"

namespace aiacc {
namespace op {

// ==========================================
// Neg 算子参数
// ==========================================
struct NegOpParams {
    // 张量维度
    uint32_t para_n{1};
    uint32_t para_c{1};
    uint32_t para_h{1};
    uint32_t para_w{8};

    // 数据类型: 0=FP32, 1=FP16
    uint8_t para_dtype{0};

    // ===== UR Bank 配置 =====
    // input
    uint8_t para_input_bank{0};
    uint32_t para_input_addr{0};

    // 常数 0.0 (从 UR 读取)
    uint8_t para_zero_bank{1};
    uint32_t para_zero_addr{0};

    // output
    uint8_t para_output_bank{2};
    uint32_t para_output_addr{0};

    // 辅助函数
    uint32_t get_elems_per_word() const {
        return (para_dtype == 0) ? 4 : 8;  // FP32: 4, FP16: 8
    }
};

// ==========================================
// 调度条目：(abs_cycle, code)
// ==========================================
struct SchedEntry {
    uint32_t abs_cycle;
    uint32_t code;
};

// ==========================================
// Neg 算子微指令序列 (schedule-based)
// ==========================================
struct NegOpInstructions : public OpInstructionsBase {
    // 各 IQ 的调度条目
    std::vector<SchedEntry> sched_ur_r0;     // UR read port 0 (input)
    std::vector<SchedEntry> sched_ur_r1;     // UR read port 1 (zero)
    std::vector<SchedEntry> sched_ur_w0;     // UR write port 0 (output)
    std::vector<SchedEntry> sched_fpadd;     // FPADD(SUB)
    std::vector<SchedEntry> sched_rtovr_5;   // RtoVR port 5  (FPADD S0) - Zero
    std::vector<SchedEntry> sched_rtovr_6;   // RtoVR port 6  (FPADD S1) - Input
    std::vector<SchedEntry> sched_rtovr_35;  // RtoVR port 35 (UVR WDATA 0) - FPADD_D

    // 计算单元 CODE 值
    uint8_t para_fpadd_code{0};  // FPADD CODE (5 bits, is_sub=1)

    // 统计信息
    uint32_t para_total_steps{0};
    uint32_t para_total_elements{0};
    uint32_t para_total_cycles{0};

    // 实现基类接口
    std::vector<IqInstructionFile> get_iq_files() const override;
    std::string get_operator_name() const override { return "Neg"; }
    uint32_t get_total_steps() const override { return para_total_steps; }
    uint32_t get_total_elements() const override { return para_total_elements; }
    std::vector<uint8_t> serialize() const override;
};

// ==========================================
// Neg 算子编译器
// ==========================================
class NegOp : public OpBase {
public:
    NegOp() = default;
    ~NegOp() = default;

    // 实现 OpBase 接口
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

    // 将任意 bank/addr 参数规整到可执行布局，并自动规避冲突
    NegOpParams sanitize_params(const NegOpParams& params,
                                std::vector<std::string>* notes = nullptr) const;

    // 编译方法
    NegOpInstructions compile(const NegOpParams& params);
};

} // namespace op
} // namespace aiacc

#endif // NEG_OP_H
