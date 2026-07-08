#include "select_int_lsu_op.h"

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <ostream>
#include <random>

#include "../../app/fp16_utils.h"

namespace aiacc {
namespace lsu {

namespace {

void write_u16_le(std::vector<uint8_t>& out, size_t off, uint16_t v) {
    out[off + 0] = static_cast<uint8_t>(v & 0xFFu);
    out[off + 1] = static_cast<uint8_t>((v >> 8) & 0xFFu);
}

void write_u32_le(std::vector<uint8_t>& out, size_t off, uint32_t v) {
    out[off + 0] = static_cast<uint8_t>(v & 0xFFu);
    out[off + 1] = static_cast<uint8_t>((v >> 8) & 0xFFu);
    out[off + 2] = static_cast<uint8_t>((v >> 16) & 0xFFu);
    out[off + 3] = static_cast<uint8_t>((v >> 24) & 0xFFu);
}

void write_fp32(std::vector<uint8_t>& out, size_t off, float v) {
    uint32_t bits = 0;
    std::memcpy(&bits, &v, sizeof(float));
    write_u32_le(out, off, bits);
}

uint32_t effective_output_bytes(uint32_t configured_bytes,
                                uint16_t output_words) {
    if (configured_bytes != 0) return configured_bytes;
    return static_cast<uint32_t>(output_words) * 16u;
}

} // namespace

LsuComputeDesc SelectIntLsuOp::default_compute_desc(uint16_t input_words,
                                                    uint16_t output_words,
                                                    uint16_t /*source_offset_words*/) {
    if (const char* e = std::getenv("LSU_WC")) {
        int v = std::atoi(e);
        if (v > 0 && v <= 255) {
            input_words = static_cast<uint16_t>(v);
            output_words = static_cast<uint16_t>(v);
        }
    }
    LsuComputeDesc d;
    // LLAMA select_int is lowered as a DDR slice load: the selected source
    // window is loaded directly into the output UR bank, then stored back.
    d.inputs["input"] = {2, 0, input_words};
    d.output = {2, 0, output_words};
    d.num_smc = 8;
    d.smc_id_en = 7;
    d.gr_intlv_addr = 0x10000000ULL;
    d.bulk_mode = true;
    return d;
}

std::vector<op::IqInstructionFile> SelectIntLsuOp::generate_compute_iq_files() const {
    // The reliable v4.19 DDR implementation for LLAMA select_int performs the
    // selection in the load address.  No SMC arithmetic/UR-copy stage is needed.
    // The old UVR_WDATA0 direct copy path is intentionally not used here.
    return {};
}

void SelectIntLsuOp::fill_input_port_data(const std::string& name,
                                          const PortInfo& port,
                                          uint8_t smc_id,
                                          std::mt19937& rng,
                                          std::vector<uint8_t>& out) const {
    if (name != "input") {
        LsuOpBase::fill_input_port_data(name, port, smc_id, rng, out);
        return;
    }
    out.assign(static_cast<size_t>(port.word_count) * 16u, 0);
    (void)smc_id;

    if (dtype_ == app::AppDataType::FLOAT16) {
        std::uniform_real_distribution<float> dist(-2.0f, 2.0f);
        for (size_t off = 0; off + 1 < out.size(); off += 2) {
            write_u16_le(out, off, app::float32_to_float16(dist(rng)));
        }
        return;
    }

    if (dtype_ == app::AppDataType::INT32) {
        std::uniform_int_distribution<int32_t> dist(-100000, 100000);
        size_t fill_bytes = out.size();
        if (output_bytes_ != 0) {
            fill_bytes = std::min(fill_bytes, static_cast<size_t>(output_bytes_));
        }
        for (size_t off = 0; off + 3 < fill_bytes; off += 4) {
            write_u32_le(out, off, static_cast<uint32_t>(dist(rng)));
        }
        return;
    }

    std::uniform_real_distribution<float> dist(-2.0f, 2.0f);
    for (size_t off = 0; off + 3 < out.size(); off += 4) {
        write_fp32(out, off, dist(rng));
    }
}

void SelectIntLsuOp::generate_golden_for_smc(
    uint8_t /*smc_id*/,
    const std::map<std::string, std::vector<uint8_t>>& input_datas,
    std::vector<uint8_t>& golden_out) const {

    auto it = input_datas.find("input");
    if (it == input_datas.end()) {
        golden_out.clear();
        return;
    }

    const auto& inp = it->second;
    // The HBM input window already starts at the selected tensor slice.
    const size_t src_off = 0;
    const size_t out_bytes = effective_output_bytes(output_bytes_, desc_.output.word_count);
    const size_t out_lines = static_cast<size_t>(desc_.output.word_count) * 16u;
    // This file is also used as final-UR golden by RTL compare.  The DDR
    // operator output is the first out_words lines, but the UR bank keeps the
    // whole loaded source window after store.
    golden_out.assign(std::max(inp.size(), std::max(out_bytes, out_lines)), 0);
    if (src_off >= inp.size()) {
        return;
    }
    const size_t copy_bytes = inp.size() - src_off;
    std::copy(inp.begin() + src_off, inp.begin() + src_off + copy_bytes, golden_out.begin());
}

void SelectIntLsuOp::write_extra_meta_json(std::ostream& os) const {
    const char* dtype = "fp32";
    if (dtype_ == app::AppDataType::FLOAT16) dtype = "fp16";
    else if (dtype_ == app::AppDataType::INT32) dtype = "int32";
    os << "  \"dtype\": \"" << dtype << "\",\n";
    os << "  \"valid_output_bytes\": "
       << effective_output_bytes(output_bytes_, desc_.output.word_count) << ",\n";
}

} // namespace lsu
} // namespace aiacc
