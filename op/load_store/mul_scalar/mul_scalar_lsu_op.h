#ifndef MUL_SCALAR_LSU_OP_V2_H
#define MUL_SCALAR_LSU_OP_V2_H

#include "../lsu_op_base.h"
#include "../../op/mul_scalar/mul_scalar_op.h"

namespace aiacc {
namespace lsu {

class MulScalarLsuOp : public LsuOpBase {
public:
    explicit MulScalarLsuOp(op::MulScalarDataType dtype = op::MulScalarDataType::FP32)
        : dtype_(dtype) {}
    ~MulScalarLsuOp() override = default;

    std::string get_name() const override { return "mul_scalar"; }

    void set_dtype(op::MulScalarDataType dtype) { dtype_ = dtype; }
    op::MulScalarDataType dtype() const { return dtype_; }

    std::vector<op::IqInstructionFile> generate_compute_iq_files() const override;

    void generate_golden_for_smc(
        uint8_t smc_id,
        const std::map<std::string, std::vector<uint8_t>>& input_datas,
        std::vector<uint8_t>& golden_out) const override;

    static LsuComputeDesc default_compute_desc(uint16_t input_words = 255);

protected:
    void fill_input_port_data(const std::string& name,
                              const PortInfo& port,
                              uint8_t smc_id,
                              std::mt19937& rng,
                              std::vector<uint8_t>& out) const override;
    void write_extra_meta_json(std::ostream& os) const override;

private:
    op::MulScalarDataType dtype_{op::MulScalarDataType::FP32};
    float scalar_value_{1.25f};
};

} // namespace lsu
} // namespace aiacc

#endif // MUL_SCALAR_LSU_OP_V2_H
