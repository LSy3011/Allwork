#include <iostream>
#include <string>

#include "mul_scalar_lsu_op.h"

int main(int argc, char* argv[]) {
    std::string case_root = "/home/juno1023/Jianyuan-release-v4.19/test_atomic_ops/verification_v2/mul_scalar_lsu_v3_test";
    std::string hbm_dir = "/home/juno1023/Jianyuan-release-v4.19/data_engine/sram";
    uint32_t seed = 12345;
    std::string dtype = "fp32";
    uint16_t words = 255;

    if (argc > 1) case_root = argv[1];
    if (argc > 2) hbm_dir = argv[2];
    if (argc > 3) seed = static_cast<uint32_t>(std::stoul(argv[3]));
    if (argc > 4) dtype = argv[4];
    if (argc > 5) words = static_cast<uint16_t>(std::stoul(argv[5]));

    const auto dt = (dtype == "fp16")
        ? aiacc::op::MulScalarDataType::FP16
        : aiacc::op::MulScalarDataType::FP32;

    aiacc::lsu::MulScalarLsuOp op(dt);
    op.set_compute_desc(aiacc::lsu::MulScalarLsuOp::default_compute_desc(words));

    aiacc::lsu::LsuCaseConfig cfg;
    cfg.case_name = "mul_scalar_lsu_v3_test";
    cfg.case_root = case_root;
    cfg.hbm_init_dir = hbm_dir;
    cfg.seed_base = seed;
    op.set_case_config(cfg);

    if (!op.assemble_full_case()) {
        std::cerr << "ERROR: assemble_full_case failed" << std::endl;
        return 1;
    }

    std::cout << "SUCCESS: MulScalar LSU case at " << case_root
              << " dtype=" << dtype << " words=" << words << std::endl;
    return 0;
}
