#include <iostream>
#include <string>
#include <cstdlib>

#include "select_int_lsu_op.h"

int main(int argc, char* argv[]) {
    std::string case_root = "/home/juno1023/Jianyuan-release-v4.19/test_atomic_ops/verification_v2/select_int_lsu_v3_test";
    std::string hbm_dir;
    uint32_t seed = 23456;
    uint16_t input_words = 96;
    uint16_t output_words = 32;
    uint16_t source_offset_words = 0;
    std::string dtype = "fp32";
    uint32_t output_bytes = 0;

    if (argc > 1) case_root = argv[1];
    if (argc > 2) hbm_dir = argv[2];
    if (argc > 3) seed = static_cast<uint32_t>(std::stoul(argv[3]));
    if (argc > 4) input_words = static_cast<uint16_t>(std::stoul(argv[4]));
    if (argc > 5) output_words = static_cast<uint16_t>(std::stoul(argv[5]));
    if (argc > 6) source_offset_words = static_cast<uint16_t>(std::stoul(argv[6]));
    if (argc > 7) dtype = argv[7];
    if (argc > 8) output_bytes = static_cast<uint32_t>(std::stoul(argv[8]));

    if (hbm_dir.empty()) {
        hbm_dir = case_root + "/hbm_data";
    }
    const int mkdir_rc = std::system((std::string("mkdir -p \"") + hbm_dir + "\"").c_str());
    if (mkdir_rc != 0) {
        std::cerr << "ERROR: failed to create hbm_dir: " << hbm_dir << std::endl;
        return 1;
    }

    if (output_words > input_words) {
        std::cerr << "ERROR: output_words exceeds input_words" << std::endl;
        return 1;
    }
    if (output_bytes != 0 && output_bytes > static_cast<uint32_t>(output_words) * 16u) {
        std::cerr << "ERROR: output_bytes exceeds output_words capacity" << std::endl;
        return 1;
    }

    aiacc::app::AppDataType dt = aiacc::app::AppDataType::FLOAT32;
    if (dtype == "fp16") {
        dt = aiacc::app::AppDataType::FLOAT16;
    } else if (dtype == "int32") {
        dt = aiacc::app::AppDataType::INT32;
    } else if (dtype != "fp32") {
        std::cerr << "ERROR: dtype must be fp32/fp16/int32" << std::endl;
        return 1;
    }

    aiacc::lsu::SelectIntLsuOp op;
    op.set_source_offset_words(source_offset_words);
    op.set_dtype(dt);
    op.set_output_bytes(output_bytes);
    op.set_compute_desc(aiacc::lsu::SelectIntLsuOp::default_compute_desc(
        input_words, output_words, source_offset_words));

    aiacc::lsu::LsuCaseConfig cfg;
    cfg.case_name = "select_int_lsu_v3_test";
    cfg.case_root = case_root;
    cfg.hbm_init_dir = hbm_dir;
    cfg.seed_base = seed;
    op.set_case_config(cfg);

    if (!op.assemble_full_case()) {
        std::cerr << "ERROR: assemble_full_case failed" << std::endl;
        return 1;
    }

    std::cout << "SUCCESS: SelectInt LSU case at " << case_root
              << " input_words=" << input_words
              << " output_words=" << output_words
              << " source_offset_words=" << source_offset_words
              << " dtype=" << dtype
              << " output_bytes=" << output_bytes
              << " hbm_dir=" << hbm_dir << std::endl;
    return 0;
}
