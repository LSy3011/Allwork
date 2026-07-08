#include "mul_scalar_lsu_op.h"

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <ostream>
#include <random>

#include "../../app/fp16_utils.h"
#include "../../iq/iq_id_defs.h"

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

uint16_t read_u16_le(const std::vector<uint8_t>& data, size_t off) {
    return static_cast<uint16_t>(data[off]) |
           static_cast<uint16_t>(static_cast<uint16_t>(data[off + 1]) << 8);
}

uint32_t read_u32_le(const std::vector<uint8_t>& data, size_t off) {
    return static_cast<uint32_t>(data[off]) |
           (static_cast<uint32_t>(data[off + 1]) << 8) |
           (static_cast<uint32_t>(data[off + 2]) << 16) |
           (static_cast<uint32_t>(data[off + 3]) << 24);
}

bool is_lsu_iq(uint32_t id) {
    return id == iq::IQ_ID_LDB || id == iq::IQ_ID_LDBAXI ||
           id == iq::IQ_ID_STB || id == iq::IQ_ID_STBAXI;
}

bool fp16_is_zero_denorm_or_min_norm(uint16_t bits) {
    const uint16_t exp = static_cast<uint16_t>((bits >> 10) & 0x1Fu);
    const uint16_t mant = static_cast<uint16_t>(bits & 0x03FFu);
    return exp == 0 || (exp == 1 && mant == 0);
}

uint16_t fp16_mul_like_rtl_non_ieee(uint16_t a_bits, uint16_t b_bits) {
    if (fp16_is_zero_denorm_or_min_norm(a_bits) ||
        fp16_is_zero_denorm_or_min_norm(b_bits)) {
        return static_cast<uint16_t>((a_bits ^ b_bits) & 0x8000u);
    }

    const float r = app::float16_to_float32(a_bits) * app::float16_to_float32(b_bits);
    return app::float32_to_float16(r);
}

} // namespace

LsuComputeDesc MulScalarLsuOp::default_compute_desc(uint16_t input_words) {
    if (const char* e = std::getenv("LSU_WC")) {
        int v = std::atoi(e);
        if (v > 0 && v <= 255) input_words = static_cast<uint16_t>(v);
    }
    LsuComputeDesc d;
    d.inputs["input"] = {0, 0, input_words};
    d.inputs["scalar"] = {1, 0, input_words};
    d.output = {2, 0, input_words};
    d.num_smc = 8;
    d.smc_id_en = 7;
    d.gr_intlv_addr = 0x10000000ULL;
    d.bulk_mode = true;
    return d;
}

std::vector<op::IqInstructionFile> MulScalarLsuOp::generate_compute_iq_files() const {
    op::MulScalarOpParams params;
    params.para_n = 1;
    params.para_c = 1;
    params.para_h = 1;
    params.para_dtype = dtype_;
    params.para_w = desc_.output.word_count * params.get_elems_per_word();

    auto it_input = desc_.inputs.find("input");
    auto it_scalar = desc_.inputs.find("scalar");
    if (it_input != desc_.inputs.end()) {
        params.para_input_bank = it_input->second.bank;
        params.para_input_addr = static_cast<uint8_t>(it_input->second.word_offset);
    }
    if (it_scalar != desc_.inputs.end()) {
        params.para_other_bank = it_scalar->second.bank;
        params.para_other_addr = static_cast<uint8_t>(it_scalar->second.word_offset);
    }
    params.para_output_bank = desc_.output.bank;
    params.para_output_addr = static_cast<uint8_t>(desc_.output.word_offset);
    params.para_smc_id_en = desc_.smc_id_en;
    params.para_identity_scalar = false;

    op::MulScalarOp op;
    auto insts = op.compile(params);
    auto all = insts.get_iq_files();

    std::vector<op::IqInstructionFile> compute;
    for (auto& f : all) {
        if (!is_lsu_iq(f.para_iq_id)) {
            compute.push_back(std::move(f));
        }
    }
    return compute;
}

void MulScalarLsuOp::fill_input_port_data(const std::string& name,
                                          const PortInfo& port,
                                          uint8_t smc_id,
                                          std::mt19937& rng,
                                          std::vector<uint8_t>& out) const {
    out.assign(static_cast<size_t>(port.word_count) * 16u, 0);

    if (name == "scalar") {
        if (dtype_ == op::MulScalarDataType::FP16) {
            const uint16_t scalar = app::float32_to_float16(scalar_value_);
            for (size_t off = 0; off + 1 < out.size(); off += 2) {
                write_u16_le(out, off, scalar);
            }
        } else {
            uint32_t scalar_bits = 0;
            std::memcpy(&scalar_bits, &scalar_value_, sizeof(float));
            for (size_t off = 0; off + 3 < out.size(); off += 4) {
                write_u32_le(out, off, scalar_bits);
            }
        }
        return;
    }

    std::uniform_real_distribution<float> dist(-2.0f, 2.0f);
    (void)smc_id;
    if (dtype_ == op::MulScalarDataType::FP16) {
        for (size_t off = 0; off + 1 < out.size(); off += 2) {
            const uint16_t bits = app::float32_to_float16(dist(rng));
            write_u16_le(out, off, bits);
        }
    } else {
        for (size_t off = 0; off + 3 < out.size(); off += 4) {
            const float v = dist(rng);
            uint32_t bits = 0;
            std::memcpy(&bits, &v, sizeof(float));
            write_u32_le(out, off, bits);
        }
    }
}

void MulScalarLsuOp::generate_golden_for_smc(
    uint8_t /*smc_id*/,
    const std::map<std::string, std::vector<uint8_t>>& input_datas,
    std::vector<uint8_t>& golden_out) const {

    auto it_inp = input_datas.find("input");
    auto it_scalar = input_datas.find("scalar");
    if (it_inp == input_datas.end() || it_scalar == input_datas.end()) {
        golden_out.clear();
        return;
    }

    const auto& inp = it_inp->second;
    const auto& scalar = it_scalar->second;
    golden_out.assign(static_cast<size_t>(desc_.output.word_count) * 16u, 0);

    if (dtype_ == op::MulScalarDataType::FP16) {
        const uint32_t elems = static_cast<uint32_t>(std::min(inp.size(), golden_out.size()) / 2);
        for (uint32_t i = 0; i < elems; ++i) {
            const uint16_t a_bits = read_u16_le(inp, static_cast<size_t>(i) * 2u);
            const uint16_t s_bits = read_u16_le(scalar, static_cast<size_t>(i % 8u) * 2u);
            write_u16_le(golden_out,
                         static_cast<size_t>(i) * 2u,
                         fp16_mul_like_rtl_non_ieee(a_bits, s_bits));
        }
        return;
    }

    const uint32_t elems = static_cast<uint32_t>(std::min(inp.size(), golden_out.size()) / 4);
    for (uint32_t i = 0; i < elems; ++i) {
        const uint32_t a_bits = read_u32_le(inp, static_cast<size_t>(i) * 4u);
        const uint32_t s_bits = read_u32_le(scalar, static_cast<size_t>(i % 4u) * 4u);
        float a = 0.0f;
        float s = 0.0f;
        std::memcpy(&a, &a_bits, sizeof(float));
        std::memcpy(&s, &s_bits, sizeof(float));
        const float r = a * s;
        uint32_t r_bits = 0;
        std::memcpy(&r_bits, &r, sizeof(float));
        write_u32_le(golden_out, static_cast<size_t>(i) * 4u, r_bits);
    }
}

void MulScalarLsuOp::write_extra_meta_json(std::ostream& os) const {
    os << "  \"dtype\": \""
       << (dtype_ == op::MulScalarDataType::FP16 ? "fp16" : "fp32") << "\",\n";
    os << "  \"valid_output_bytes\": "
       << static_cast<uint32_t>(desc_.output.word_count) * 16u << ",\n";
}

} // namespace lsu
} // namespace aiacc
