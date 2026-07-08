#ifndef SELECT_INT_LSU_OP_V2_H
#define SELECT_INT_LSU_OP_V2_H

#include "../lsu_op_base.h"
#include "../../op/select_int/select_int_op.h"

namespace aiacc {
namespace lsu {

class SelectIntLsuOp : public LsuOpBase {
public:
    SelectIntLsuOp() = default;
    ~SelectIntLsuOp() override = default;

    std::string get_name() const override { return "select_int"; }

    void set_source_offset_words(uint16_t words) { source_offset_words_ = words; }
    uint16_t source_offset_words() const { return source_offset_words_; }

    void set_dtype(app::AppDataType dtype) { dtype_ = dtype; }
    app::AppDataType dtype() const { return dtype_; }

    void set_output_bytes(uint32_t bytes) { output_bytes_ = bytes; }
    uint32_t output_bytes() const { return output_bytes_; }

    std::vector<op::IqInstructionFile> generate_compute_iq_files() const override;

    void generate_golden_for_smc(
        uint8_t smc_id,
        const std::map<std::string, std::vector<uint8_t>>& input_datas,
        std::vector<uint8_t>& golden_out) const override;

    static LsuComputeDesc default_compute_desc(uint16_t input_words = 96,
                                               uint16_t output_words = 32,
                                               uint16_t source_offset_words = 0);

protected:
    void fill_input_port_data(const std::string& name,
                              const PortInfo& port,
                              uint8_t smc_id,
                              std::mt19937& rng,
                              std::vector<uint8_t>& out) const override;
    void write_extra_meta_json(std::ostream& os) const override;

private:
    uint16_t source_offset_words_{0};
    app::AppDataType dtype_{app::AppDataType::FLOAT32};
    uint32_t output_bytes_{0};
};

} // namespace lsu
} // namespace aiacc

#endif // SELECT_INT_LSU_OP_V2_H
