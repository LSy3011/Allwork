#include "cos_op.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <iostream>
#include <map>
#include <set>

#include "../../app/fp16_utils.h"
#include "../../iq/iq_id_defs.h"
#include "../../iq/modules/fpadd_iq.h"
#include "../../iq/modules/fpmul_iq.h"
#include "../common/op_registry.h"

namespace aiacc {
namespace op {

namespace {

constexpr uint8_t kRtovrInDrFpMulD = 3;
constexpr uint8_t kRtovrInDrFpAddD = 4;
constexpr uint8_t kRtovrInUrRdata0 = 30;

constexpr uint32_t kCoeffTerms = 9;      // c0..c8
constexpr uint8_t kMaxDlyField = 7;

static inline float f32(float v) {
    float out;
    std::memcpy(&out, &v, sizeof(float));
    return out;
}

static inline uint32_t ur_read_code(uint8_t bank, uint32_t addr) {
    return 1u | ((uint32_t)(bank & 0x7u) << 1) | ((addr & 0xFFu) << 4);
}

static inline uint32_t ur_read_idle_code(uint8_t bank) {
    return ((uint32_t)(bank & 0x7u) << 1);
}

static inline bool ur_read_enabled(uint32_t code) {
    return (code & 1u) != 0u;
}

static inline uint8_t ur_read_bank(uint32_t code) {
    return static_cast<uint8_t>((code >> 1) & 0x7u);
}

static inline uint32_t ur_write_code(uint8_t bank, uint32_t addr, uint16_t wbe = 0xFFFF) {
    return 1u | ((uint32_t)(bank & 0x7u) << 1) |
           ((addr & 0xFFu) << 4) | ((uint32_t)(wbe & 0xFFFFu) << 12);
}

struct CosUrReadReq {
    uint8_t port{0};
    uint32_t phase{0};
    uint8_t bank{0};
    uint32_t code{0};
};

static void push_ur_read_port(CosOpInstructions& result, uint8_t port,
                              uint32_t abs_cycle, uint32_t code) {
    switch (port) {
    case 0:
        result.sched_ur_r0.push_back({abs_cycle, code});
        break;
    case 1:
        result.sched_ur_r1.push_back({abs_cycle, code});
        break;
    case 2:
        result.sched_ur_r2.push_back({abs_cycle, code});
        break;
    default:
        break;
    }
}

static void emit_ur_reads(CosOpInstructions& result,
                                             uint32_t cursor,
                                             const std::vector<CosUrReadReq>& reads) {
    std::vector<CosUrReadReq> per_port[3];
    for (const auto& read : reads)
        if (read.port < 3)
            per_port[read.port].push_back(read);
    for (uint8_t port = 0; port < 3; ++port) {
        std::sort(per_port[port].begin(), per_port[port].end(),
                  [](const CosUrReadReq& a, const CosUrReadReq& b) {
                      return a.phase < b.phase;
                  });
        for (const auto& read : per_port[port])
            push_ur_read_port(result, port, cursor + read.phase, read.code);
    }
}

static inline uint32_t rtovr_code(uint8_t source_id) {
    return 1u | ((uint32_t)(source_id & 0x3Fu) << 1);
}

static inline uint8_t ur_source_for_port(uint8_t port) {
    return static_cast<uint8_t>(kRtovrInUrRdata0 + (port & 0x3u));
}

static std::vector<float> cos_taylor_coeffs() {
    std::vector<float> coeffs;
    coeffs.reserve(kCoeffTerms);
    for (uint32_t n = 0; n < kCoeffTerms; ++n) {
        double fact = 1.0;
        for (uint32_t i = 1; i <= 2 * n; ++i) fact *= static_cast<double>(i);
        const double sign = (n & 1u) ? -1.0 : 1.0;
        coeffs.push_back(static_cast<float>(sign / fact));
    }
    return coeffs;
}

static float cos_taylor16_f32(float x) {
    const auto coeffs = cos_taylor_coeffs();
    const float x2 = f32(x * x);
    float acc = coeffs.back();
    for (int k = static_cast<int>(coeffs.size()) - 2; k >= 0; --k) {
        acc = f32(f32(acc * x2) + coeffs[static_cast<size_t>(k)]);
    }
    return acc;
}

static uint16_t f16_round(float v) {
    return app::float32_to_float16(v);
}

static float f16_value(uint16_t bits) {
    return app::float16_to_float32(bits);
}

static uint16_t f16_ftz(uint16_t bits) {
    const uint16_t exp = bits & 0x7C00u;
    const uint16_t mant = bits & 0x03FFu;
    return (exp == 0 && mant != 0) ? static_cast<uint16_t>(bits & 0x8000u) : bits;
}

static uint16_t f16_round_ftz(float v) {
    return f16_ftz(f16_round(v));
}

static float f16_value_ftz(uint16_t bits) {
    return f16_value(f16_ftz(bits));
}

static uint16_t cos_taylor16_f16_bits(float x) {
    const auto coeffs = cos_taylor_coeffs();
    const uint16_t x_bits = f16_round_ftz(x);
    const float x_val = f16_value_ftz(x_bits);
    const uint16_t x2_bits = f16_round_ftz(x_val * x_val);
    const float x2_val = f16_value_ftz(x2_bits);

    uint16_t acc_bits = f16_round_ftz(coeffs.back());
    for (int k = static_cast<int>(coeffs.size()) - 2; k >= 0; --k) {
        const float acc_val = f16_value_ftz(acc_bits);
        const float mul_val = f16_value_ftz(f16_round_ftz(acc_val * x2_val));
        const float coeff_val = f16_value_ftz(f16_round_ftz(coeffs[static_cast<size_t>(k)]));
        acc_bits = f16_round_ftz(mul_val + coeff_val);
    }
    return acc_bits;
}

static uint64_t serialize_header_41(uint8_t vld, uint8_t jump, uint8_t dly,
                                    uint8_t embed = 0, uint16_t loops = 0,
                                    uint8_t cond = 0, uint8_t inc = 0,
                                    uint8_t embed_end_bits = 0,
                                    uint8_t inc_embed_bits = 0) {
    uint64_t bits = 0;
    uint32_t pos = 0;
    const uint8_t embed_end = (embed == 0) ? (embed_end_bits & 0x3Fu) : 0u;
    const uint8_t inc_embed = (embed == 0) ? (inc_embed_bits & 0x3Fu) : 0u;

    bits |= (uint64_t)(inc_embed & 0x3F) << pos; pos += 6;
    bits |= (uint64_t)(inc & 0xF) << pos;       pos += 4;
    bits |= (uint64_t)(dly & 0x7) << pos;       pos += 3;
    bits |= (uint64_t)(cond & 0x7F) << pos;     pos += 7;
    bits |= (uint64_t)(loops & 0x3FF) << pos;   pos += 10;
    bits |= (uint64_t)(embed_end & 0x3F) << pos; pos += 6;
    bits |= (uint64_t)(embed & 0x7) << pos;     pos += 3;
    bits |= (uint64_t)(jump & 0x1) << pos;      pos += 1;
    bits |= (uint64_t)(vld & 0x1) << pos;       pos += 1;
    return bits;
}

static std::vector<uint8_t> pack_instruction(uint8_t vld, uint8_t jump,
                                             uint8_t dly, uint32_t code,
                                             uint32_t code_bits,
                                             uint8_t embed = 0,
                                             uint16_t loops = 0,
                                             uint8_t cond = 0,
                                             uint8_t inc = 0,
                                             uint8_t embed_end_bits = 0,
                                             uint8_t inc_embed_bits = 0) {
    const uint64_t header = serialize_header_41(vld, jump, dly, embed, loops,
                                                cond, inc, embed_end_bits,
                                                inc_embed_bits);
    const uint32_t total_bits = 41 + code_bits;
    const uint32_t num_bytes = (total_bits + 7) / 8;
    std::vector<uint8_t> bytes;
    if (total_bits <= 64) {
        const uint64_t mask = (code_bits >= 64) ? ~0ULL : ((1ULL << code_bits) - 1ULL);
        const uint64_t total = (header << code_bits) | ((uint64_t)code & mask);
        for (int i = static_cast<int>(num_bytes) - 1; i >= 0; --i)
            bytes.push_back(static_cast<uint8_t>((total >> (i * 8)) & 0xFF));
    } else {
        const unsigned __int128 mask = (static_cast<unsigned __int128>(1) << code_bits) - 1;
        const unsigned __int128 total =
            (static_cast<unsigned __int128>(header) << code_bits) |
            (static_cast<unsigned __int128>(code) & mask);
        for (int i = static_cast<int>(num_bytes) - 1; i >= 0; --i)
            bytes.push_back(static_cast<uint8_t>((total >> (i * 8)) & 0xFF));
    }
    return bytes;
}

static std::vector<uint8_t> sched_to_iq_bytes(const std::vector<CosSchedEntry>& sched,
                                              uint32_t code_bits,
                                              bool keep_route_on_pad = false) {
    std::vector<uint8_t> data;
    int32_t prev_cycle = -1;
    bool prev_code_valid = false;
    uint32_t prev_code = 0;

    for (const auto& entry : sched) {
        int32_t dly = (prev_cycle < 0)
            ? static_cast<int32_t>(entry.abs_cycle)
            : static_cast<int32_t>(entry.abs_cycle) - prev_cycle - 1;
        while (dly > static_cast<int32_t>(kMaxDlyField)) {
            const uint32_t pad = (keep_route_on_pad && prev_code_valid) ? prev_code : 0u;
            auto nop = pack_instruction(1, 0, kMaxDlyField, pad, code_bits);
            data.insert(data.end(), nop.begin(), nop.end());
            dly -= 8;
        }
        auto inst = pack_instruction(1, 0, static_cast<uint8_t>(dly), entry.code, code_bits);
        data.insert(data.end(), inst.begin(), inst.end());
        prev_cycle = static_cast<int32_t>(entry.abs_cycle);
        prev_code = entry.code;
        prev_code_valid = true;
    }

    const uint32_t end_code = (keep_route_on_pad && prev_code_valid) ? prev_code : 0u;
    auto end_inst = pack_instruction(1, 1, 0, end_code, code_bits);
    data.insert(data.end(), end_inst.begin(), end_inst.end());
    return data;
}

static bool sched_to_embed_slot_block_iq_bytes(const std::vector<CosSchedEntry>& sched,
                                               uint32_t code_bits,
                                               uint32_t total_cycles,
                                               uint32_t block_cycles,
                                               std::vector<uint8_t>& data,
                                               bool hold_last_code = false,
                                               bool step_addr_inc = false) {
    data.clear();
    if (sched.empty() || total_cycles == 0 || block_cycles == 0)
        return false;
    if ((total_cycles % block_cycles) != 0)
        return false;

    const uint32_t block_count = total_cycles / block_cycles;
    if (block_count == 0 || block_count > 1024)
        return false;

    std::vector<uint32_t> slot_codes(block_cycles, 0u);
    std::vector<bool> slot_used(block_cycles, false);
    std::vector<std::set<uint32_t>> seen_offsets(block_count);
    std::set<uint32_t> expected_offsets;

    auto normalize_step_addr = [&](uint32_t code, uint32_t block, uint32_t& norm_code) -> bool {
        norm_code = code;
        if (!step_addr_inc || (code & 1u) == 0)
            return true;
        const uint32_t addr = (code >> 4) & 0xFFu;
        // Normalize each replayed block back to the block-0 base. The EMBED
        // INC advances the address between block replays.
        const uint32_t base_addr = (addr - block) & 0xFFu;
        norm_code = (code & ~(0xFFu << 4)) | (base_addr << 4);
        return true;
    };

    for (const auto& entry : sched) {
        const uint32_t block = entry.abs_cycle / block_cycles;
        const uint32_t phase = entry.abs_cycle % block_cycles;
        if (block >= block_count)
            return false;

        const uint32_t offset = phase;
        uint32_t norm_code = 0;
        if (!normalize_step_addr(entry.code, block, norm_code))
            return false;

        if (block == 0) {
            if (slot_used[offset] && slot_codes[offset] != norm_code)
                return false;
            slot_codes[offset] = norm_code;
            slot_used[offset] = true;
            expected_offsets.insert(offset);
        } else {
            if (!slot_used[offset] || slot_codes[offset] != norm_code)
                return false;
        }
        seen_offsets[block].insert(offset);
    }

    for (const auto& offsets : seen_offsets) {
        if (offsets != expected_offsets)
            return false;
    }

    if (hold_last_code) {
        uint32_t current = 0;
        for (uint32_t slot = 0; slot < block_cycles; ++slot) {
            if (slot_used[slot])
                current = slot_codes[slot];
            else
                slot_codes[slot] = current;
        }
    }

    const uint16_t loops = static_cast<uint16_t>(block_count - 1);
    auto loop_param = pack_instruction(1, 0, 0, 0, code_bits,
                                       1u, loops, 0, step_addr_inc ? 1u : 0u, 0u, 0u);
    data.insert(data.end(), loop_param.begin(), loop_param.end());

    for (uint32_t slot = 0; slot < block_cycles;) {
        uint32_t run_len = 1;
        while (slot + run_len < block_cycles &&
               slot_codes[slot + run_len] == slot_codes[slot] &&
               run_len < 1024) {
            ++run_len;
        }
        const uint8_t embed_end = (slot + run_len == block_cycles) ? 1u : 0u;
        const uint8_t inc_embed =
            (step_addr_inc && (((slot_codes[slot] & 1u) != 0u) || embed_end != 0u)) ? 1u : 0u;
        auto inst = pack_instruction(1, 0, 0, slot_codes[slot], code_bits,
                                     0u, static_cast<uint16_t>(run_len - 1),
                                     0, 0, embed_end, inc_embed);
        data.insert(data.end(), inst.begin(), inst.end());
        slot += run_len;
    }

    auto end_inst = pack_instruction(1, 1, 0, 0, code_bits);
    data.insert(data.end(), end_inst.begin(), end_inst.end());
    return true;
}

static bool sched_to_cos_fpmul_stage_iq_bytes(const std::vector<CosSchedEntry>& sched,
                                              uint32_t code_bits,
                                              uint32_t total_cycles,
                                              uint32_t stage_cycles,
                                              std::vector<uint8_t>& data) {
    data.clear();
    if (sched.empty() || total_cycles == 0 || stage_cycles == 0 ||
        (total_cycles % stage_cycles) != 0) {
        return false;
    }

    const uint32_t total_stages = total_cycles / stage_cycles;
    if (total_stages == 0)
        return false;

    std::vector<uint8_t> stage_seen(total_stages, 0u);
    uint32_t fpmul_code = sched.front().code;
    for (const auto& entry : sched) {
        if (entry.code != fpmul_code)
            return false;
        const uint32_t stage = entry.abs_cycle / stage_cycles;
        const uint32_t phase = entry.abs_cycle % stage_cycles;
        if (stage >= total_stages)
            return false;
        if (phase == 15) {
            stage_seen[stage] |= 1u;
        } else if (phase == 25) {
            stage_seen[stage] |= 2u;
        } else {
            return false;
        }
    }

    for (uint8_t seen : stage_seen) {
        if (seen != 3u)
            return false;
    }

    uint32_t remaining = total_stages;
    while (remaining > 0) {
        const uint32_t chunk = std::min<uint32_t>(remaining, 1024u);
        const uint16_t loops = static_cast<uint16_t>(chunk - 1u);

        auto loop_param = pack_instruction(1, 0, 0, 0, code_bits, 1u, loops);
        data.insert(data.end(), loop_param.begin(), loop_param.end());

        auto leading_nop = pack_instruction(1, 0, 0, 0, code_bits, 0u, 14u);
        data.insert(data.end(), leading_nop.begin(), leading_nop.end());

        auto first_mul = pack_instruction(1, 0, 0, fpmul_code, code_bits);
        data.insert(data.end(), first_mul.begin(), first_mul.end());

        auto between_nop = pack_instruction(1, 0, 0, 0, code_bits, 0u, 8u);
        data.insert(data.end(), between_nop.begin(), between_nop.end());

        auto second_mul = pack_instruction(1, 0, 0, fpmul_code, code_bits);
        data.insert(data.end(), second_mul.begin(), second_mul.end());

        const uint32_t tail_len = stage_cycles - 26u;
        auto tail_nop = pack_instruction(1, 0, 0, 0, code_bits, 0u,
                                         static_cast<uint16_t>(tail_len - 1u),
                                         0, 0, 1u, 0u);
        data.insert(data.end(), tail_nop.begin(), tail_nop.end());

        remaining -= chunk;
    }

    auto end_inst = pack_instruction(1, 1, 0, 0, code_bits);
    data.insert(data.end(), end_inst.begin(), end_inst.end());
    return true;
}

static bool sched_can_use_step_addr_inc(const std::vector<CosSchedEntry>& sched,
                                        uint32_t total_cycles,
                                        uint32_t block_cycles) {
    if (sched.empty() || total_cycles == 0 || block_cycles == 0 ||
        (total_cycles % block_cycles) != 0) {
        return false;
    }
    const uint32_t block_count = total_cycles / block_cycles;
    if (block_count <= 1)
        return true;

    std::map<uint32_t, uint32_t> phase_base_addr;
    for (const auto& entry : sched) {
        if ((entry.code & 1u) == 0u)
            continue;
        const uint32_t block = entry.abs_cycle / block_cycles;
        const uint32_t phase = entry.abs_cycle % block_cycles;
        if (block >= block_count)
            return false;
        const uint32_t addr = (entry.code >> 4) & 0xFFu;
        const uint32_t base_addr = (addr - block) & 0xFFu;
        auto inserted = phase_base_addr.emplace(phase, base_addr);
        if (!inserted.second && inserted.first->second != base_addr)
            return false;
    }
    return !phase_base_addr.empty();
}

static uint8_t choose_ur_parking_bank(const std::set<uint8_t>& avoid_banks) {
    for (uint8_t bank = 0; bank < 8; ++bank)
        if (avoid_banks.count(bank) == 0)
            return bank;
    return 0;
}

static std::array<std::vector<CosSchedEntry>, 3>
add_ur_read_parking(const std::vector<CosSchedEntry>& r0,
                    const std::vector<CosSchedEntry>& r1,
                    const std::vector<CosSchedEntry>& r2,
                    uint32_t total_cycles) {
    std::array<std::map<uint32_t, uint32_t>, 3> by_cycle;
    const std::array<const std::vector<CosSchedEntry>*, 3> inputs = {&r0, &r1, &r2};
    std::set<uint32_t> active_cycles;

    for (uint8_t port = 0; port < 3; ++port) {
        for (const auto& entry : *inputs[port]) {
            auto inserted = by_cycle[port].emplace(entry.abs_cycle, entry.code);
            if (!inserted.second && inserted.first->second != entry.code) {
                std::cerr << "[CosOp][WARN] duplicate UR-R" << static_cast<int>(port)
                          << " cycle " << entry.abs_cycle << " with different codes; "
                          << "keeping the first code" << std::endl;
            }
            active_cycles.insert(entry.abs_cycle);
        }
    }

    std::array<std::vector<CosSchedEntry>, 3> adjusted;
    uint32_t false_block_fixes = 0;
    uint32_t true_conflicts = 0;
    std::set<uint8_t> global_active_banks;
    std::array<bool, 3> dense_parking = {false, false, false};
    bool port0_reads_bank0 = false;

    for (uint8_t port = 0; port < 3; ++port) {
        for (const auto& [cycle, code] : by_cycle[port]) {
            (void)cycle;
            if (!ur_read_enabled(code))
                continue;
            const uint8_t bank = ur_read_bank(code);
            global_active_banks.insert(bank);
            if (port == 0 && bank == 0)
                port0_reads_bank0 = true;
        }
    }
    if (port0_reads_bank0) {
        for (uint8_t port = 1; port < 3; ++port) {
            for (const auto& [cycle, code] : by_cycle[port]) {
                (void)cycle;
                if (!ur_read_enabled(code) || ur_read_bank(code) != 0)
                    continue;
                for (uint8_t higher = 0; higher < port; ++higher)
                    dense_parking[higher] = true;
            }
        }
    }
    const uint8_t dense_park_bank = choose_ur_parking_bank(global_active_banks);

    std::set<uint32_t> cycles_to_emit = active_cycles;
    if (total_cycles > 0) {
        for (uint8_t port = 0; port < 3; ++port) {
            if (!dense_parking[port])
                continue;
            for (uint32_t cycle = 0; cycle < total_cycles; ++cycle)
                cycles_to_emit.insert(cycle);
        }
    }

    for (uint32_t cycle : cycles_to_emit) {
        std::array<bool, 3> active = {false, false, false};
        std::array<uint32_t, 3> code = {0u, 0u, 0u};
        std::set<uint8_t> active_banks;

        for (uint8_t port = 0; port < 3; ++port) {
            auto it = by_cycle[port].find(cycle);
            if (it == by_cycle[port].end())
                continue;
            active[port] = ur_read_enabled(it->second);
            code[port] = it->second;
            if (active[port]) {
                const uint8_t bank = ur_read_bank(it->second);
                if (!active_banks.insert(bank).second)
                    ++true_conflicts;
            }
        }

        for (uint8_t port = 0; port < 3; ++port) {
            if (active[port]) {
                adjusted[port].push_back({cycle, code[port]});
                continue;
            }

            std::set<uint8_t> lower_active_banks;
            for (uint8_t lower = static_cast<uint8_t>(port + 1); lower < 3; ++lower) {
                if (active[lower])
                    lower_active_banks.insert(ur_read_bank(code[lower]));
            }
            if (lower_active_banks.count(0) == 0 && !dense_parking[port])
                continue;

            const uint8_t park_bank = lower_active_banks.empty()
                ? dense_park_bank
                : choose_ur_parking_bank(active_banks);
            adjusted[port].push_back({cycle, ur_read_idle_code(park_bank)});
            ++false_block_fixes;
        }
    }

    for (auto& sched : adjusted) {
        std::sort(sched.begin(), sched.end(),
                  [](const CosSchedEntry& a, const CosSchedEntry& b) {
                      return a.abs_cycle < b.abs_cycle;
                  });
    }

    if (false_block_fixes > 0) {
        std::cout << "[CosOp] Inserted " << false_block_fixes
                  << " UR read parking slots for disabled-port bank arbitration"
                  << std::endl;
    }
    if (true_conflicts > 0) {
        std::cout << "[CosOp][WARN] Detected " << true_conflicts
                  << " same-cycle active UR read bank conflicts. Cos scheduling "
                  << "should avoid these with DLY phase separation." << std::endl;
    }

    return adjusted;
}

template <typename T>
static void append_scalar(std::vector<uint8_t>& out, T bits, size_t bytes) {
    for (size_t i = 0; i < bytes; ++i)
        out.push_back(static_cast<uint8_t>((bits >> (i * 8)) & 0xFF));
}

} // namespace

std::string CosOp::get_name() const { return "cos"; }

CosOpParams CosOp::parse_request(const app::AppOperatorRequest& req) const {
    CosOpParams p;
    p.para_n = req.para_dimensions.para_n;
    p.para_c = req.para_dimensions.para_c;
    p.para_h = req.para_dimensions.para_h;
    p.para_w = req.para_dimensions.para_w;

    auto input_it = req.para_inputs.find("input_tensor");
    if (input_it != req.para_inputs.end() &&
        input_it->second.para_dtype == app::AppDataType::FLOAT16) {
        p.para_dtype = 1;
    }

    const uint32_t total_elems = p.para_n * p.para_c * p.para_h * p.para_w;
    const uint32_t total_steps = (total_elems + p.get_elems_per_word() - 1) / p.get_elems_per_word();
    auto get_i32 = [&](const char* key, int def) -> int {
        auto it = req.para_parameters.find(key);
        if (it != req.para_parameters.end() && !it->second.para_data_i32.empty())
            return it->second.para_data_i32[0];
        return def;
    };

    if (req.para_layout_mode == "bank_conflict") {
        p.para_input_bank = static_cast<uint8_t>((req.para_bc_input_bank >= 0) ? req.para_bc_input_bank : 0);
        p.para_const_bank = static_cast<uint8_t>((req.para_bc_other_bank >= 0) ? req.para_bc_other_bank : 1);
        p.para_output_bank = static_cast<uint8_t>((req.para_bc_output_bank >= 0) ? req.para_bc_output_bank : 3);
        p.para_input_addr = 0;
        p.para_const_addr = (p.para_const_bank == p.para_input_bank) ? total_steps : 0;
        p.para_output_addr = 0;
        const uint32_t const_words =
            kCoeffTerms + ((p.para_const_bank == 0 && p.para_const_addr == 0) ? 1u : 0u);
        if (p.para_output_bank == p.para_input_bank) p.para_output_addr += total_steps;
        if (p.para_output_bank == p.para_const_bank && p.para_output_bank != p.para_input_bank)
            p.para_output_addr += total_steps;
        if (p.para_output_bank == p.para_const_bank) p.para_output_addr += const_words;
    } else {
        p.para_input_bank = static_cast<uint8_t>(get_i32("input_bank", 0));
        p.para_const_bank = static_cast<uint8_t>(get_i32("other_bank", 1));
        p.para_output_bank = static_cast<uint8_t>(get_i32("output_bank", 3));
        p.para_input_addr = static_cast<uint32_t>(get_i32("input_addr", 0));
        p.para_const_addr = static_cast<uint32_t>(get_i32("other_addr", 0));
        p.para_output_addr = static_cast<uint32_t>(get_i32("output_addr", 0));
    }

    p.para_taylor_terms = kCoeffTerms;
    return p;
}

CosOpInstructions CosOp::compile(const CosOpParams& params) const {
    CosOpInstructions result;
    const uint32_t total_elems = params.para_n * params.para_c * params.para_h * params.para_w;
    const uint32_t total_steps = (total_elems + params.get_elems_per_word() - 1) / params.get_elems_per_word();
    constexpr uint32_t kStageCycles = 45;
    constexpr uint32_t kBlockCycles = kStageCycles * 8;

    result.para_total_steps = total_steps;
    result.para_total_elements = total_elems;
    result.para_taylor_terms = kCoeffTerms;
    result.para_block_cycles = kBlockCycles;
    result.para_total_cycles = total_steps * kBlockCycles;

    iq::FpMulCru mul{};
    mul.para_inst_valid = true;
    mul.para_src_precision_is_32b = (params.para_dtype == 0);
    mul.para_dst_precision_is_32b = (params.para_dtype == 0);
    result.para_fpmul_code = iq::FpMulIq::pack_cru_fpmul(mul);

    iq::FpAddCru add{};
    add.para_inst_valid = true;
    add.para_src_precision_32b = (params.para_dtype == 0);
    add.para_dst_precision_32b = (params.para_dtype == 0);
    add.para_update_status = false;
    add.para_is_sub = false;
    result.para_fpadd_code = iq::FpAddIq::pack_cru_fpadd(add);

    std::cout << "[CosOp] Layout: input B" << static_cast<int>(params.para_input_bank)
              << "@" << params.para_input_addr
              << ", coeff B" << static_cast<int>(params.para_const_bank)
              << "@" << params.para_const_addr
              << ", output B" << static_cast<int>(params.para_output_bank)
              << "@" << params.para_output_addr
              << ", steps=" << total_steps
              << ", Taylor terms=" << kCoeffTerms << " (x^16)" << std::endl;

    for (uint32_t step = 0; step < total_steps; ++step) {
        const uint32_t B = step * kBlockCycles;
        uint32_t cursor = B;
        const uint32_t input_addr = params.para_input_addr + step;
        const uint32_t output_addr = params.para_output_addr + step;
        const bool split_input_ports =
            (params.para_input_bank != 0 && params.para_const_bank == 0);
        const uint8_t coeff_read_port = 2;

        // First Horner stage: acc = c8*x2 + c7. The three UR reads are
        // separated by DLY phases; idle higher-priority read ports are parked
        // later when IQ bytes are emitted.
        const uint8_t first_input_port = 0;
        const uint8_t c8_port = coeff_read_port;
        const uint8_t c7_port = coeff_read_port;
        emit_ur_reads(result, cursor, {
            {first_input_port, 0, params.para_input_bank, ur_read_code(params.para_input_bank, input_addr)},
            {c8_port, 15, params.para_const_bank, ur_read_code(params.para_const_bank, params.para_const_addr + 8)},
            {c7_port, 30, params.para_const_bank, ur_read_code(params.para_const_bank, params.para_const_addr + 7)}
        });
        result.sched_rtovr_3.push_back({cursor + 10, rtovr_code(ur_source_for_port(first_input_port))});
        result.sched_rtovr_4.push_back({cursor + 10, rtovr_code(ur_source_for_port(first_input_port))});
        result.sched_fpmul.push_back({cursor + 15, result.para_fpmul_code}); // x2
        result.sched_rtovr_3.push_back({cursor + 20, rtovr_code(ur_source_for_port(c8_port))});
        result.sched_rtovr_4.push_back({cursor + 20, rtovr_code(kRtovrInDrFpMulD)});
        result.sched_fpmul.push_back({cursor + 25, result.para_fpmul_code}); // c8*x2
        result.sched_rtovr_5.push_back({cursor + 30, rtovr_code(kRtovrInDrFpMulD)});
        result.sched_rtovr_6.push_back({cursor + 30, rtovr_code(ur_source_for_port(c7_port))});
        result.sched_fpadd.push_back({cursor + 35, result.para_fpadd_code});
        cursor += kStageCycles;

        for (int coeff = 6; coeff >= 0; --coeff) {
            const uint32_t stage_index = static_cast<uint32_t>(7 - coeff);
            const uint8_t input_port =
                split_input_ports ? static_cast<uint8_t>(stage_index & 1u) : 0;
            const bool use_c0_duplicate =
                (coeff == 0 && params.para_const_bank == 0 && params.para_const_addr == 0);
            const uint8_t coeff_port = coeff_read_port;
            const uint32_t coeff_addr =
                use_c0_duplicate ? (params.para_const_addr + kCoeffTerms)
                                 : (params.para_const_addr + coeff);
            emit_ur_reads(result, cursor, {
                {input_port, 0, params.para_input_bank, ur_read_code(params.para_input_bank, input_addr)},
                {coeff_port, 30, params.para_const_bank, ur_read_code(params.para_const_bank, coeff_addr)}
            });
            result.sched_rtovr_3.push_back({cursor + 10, rtovr_code(ur_source_for_port(input_port))});
            result.sched_rtovr_4.push_back({cursor + 10, rtovr_code(ur_source_for_port(input_port))});
            result.sched_fpmul.push_back({cursor + 15, result.para_fpmul_code}); // x2
            result.sched_rtovr_3.push_back({cursor + 20, rtovr_code(kRtovrInDrFpAddD)});
            result.sched_rtovr_4.push_back({cursor + 20, rtovr_code(kRtovrInDrFpMulD)});
            result.sched_fpmul.push_back({cursor + 25, result.para_fpmul_code}); // acc*x2
            result.sched_rtovr_5.push_back({cursor + 30, rtovr_code(kRtovrInDrFpMulD)});
            result.sched_rtovr_6.push_back({cursor + 30, rtovr_code(ur_source_for_port(coeff_port))});
            result.sched_fpadd.push_back({cursor + 35, result.para_fpadd_code});
            if (coeff == 0) {
                result.sched_rtovr_35.push_back({cursor + 40, rtovr_code(kRtovrInDrFpAddD)});
                result.sched_ur_w0.push_back({cursor + 44, ur_write_code(params.para_output_bank, output_addr)});
            }
            cursor += kStageCycles;
        }
    }

    return result;
}

OpInstructionsBase* CosOp::compile(const app::AppOperatorRequest& req) {
    CosOpInstructions* insts = new CosOpInstructions();
    *insts = compile(parse_request(req));
    return insts;
}

bool CosOp::generate_data(const app::AppOperatorRequest& req,
                          std::vector<uint8_t>& input_bin,
                          std::vector<uint8_t>& other_bin,
                          std::vector<uint8_t>& alpha_bin) {
    alpha_bin.clear();
    const auto params = parse_request(req);
    auto it = req.para_inputs.find("input_tensor");
    if (it == req.para_inputs.end()) return false;

    for (float v : it->second.para_data_f32) {
        if (params.para_dtype == 0) {
            uint32_t bits;
            std::memcpy(&bits, &v, sizeof(float));
            append_scalar(input_bin, bits, 4);
        } else {
            uint16_t bits = app::float32_to_float16(v);
            append_scalar(input_bin, bits, 2);
        }
    }

    auto append_coeff_word = [&](float c) {
        if (params.para_dtype == 0) {
            uint32_t bits;
            std::memcpy(&bits, &c, sizeof(float));
            for (uint32_t lane = 0; lane < 4; ++lane)
                append_scalar(other_bin, bits, 4);
        } else {
            uint16_t bits = app::float32_to_float16(c);
            for (uint32_t lane = 0; lane < 8; ++lane)
                append_scalar(other_bin, bits, 2);
        }
    };

    const auto coeffs = cos_taylor_coeffs();
    for (float c : coeffs) {
        append_coeff_word(c);
    }
    if (params.para_const_bank == 0 && params.para_const_addr == 0) {
        append_coeff_word(coeffs.front());
    }

    return true;
}

bool CosOp::calculate_golden(const app::AppOperatorRequest& req,
                             std::vector<uint8_t>& golden_bin) {
    auto it = req.para_inputs.find("input_tensor");
    if (it == req.para_inputs.end()) return false;
    const bool fp16 = (it->second.para_dtype == app::AppDataType::FLOAT16);

    for (float x : it->second.para_data_f32) {
        if (!fp16) {
            const float y = cos_taylor16_f32(x);
            uint32_t bits;
            std::memcpy(&bits, &y, sizeof(float));
            append_scalar(golden_bin, bits, 4);
        } else {
            uint16_t bits = cos_taylor16_f16_bits(x);
            append_scalar(golden_bin, bits, 2);
        }
    }
    return true;
}

void CosOp::fill_random_request(app::AppOperatorRequest& req,
                                std::mt19937& rng,
                                std::uniform_real_distribution<float>&,
                                const std::string& dtype_str) {
    req.para_operator = "cos";
    std::uniform_real_distribution<float> cos_dist(-3.1415926535f, 3.1415926535f);
    const uint32_t total = req.para_dimensions.para_n * req.para_dimensions.para_c *
                           req.para_dimensions.para_h * req.para_dimensions.para_w;

    app::AppTensorDesc input;
    input.para_name = "input_tensor";
    input.para_dtype = (dtype_str == "fp16") ? app::AppDataType::FLOAT16 : app::AppDataType::FLOAT32;
    input.para_shape = {req.para_dimensions.para_n, req.para_dimensions.para_c,
                        req.para_dimensions.para_h, req.para_dimensions.para_w};
    input.para_data_f32.reserve(total);
    for (uint32_t i = 0; i < total; ++i)
        input.para_data_f32.push_back(cos_dist(rng));
    req.para_inputs["input_tensor"] = input;
}

std::vector<IqInstructionFile> CosOpInstructions::get_iq_files() const {
    std::vector<IqInstructionFile> files;
    const uint32_t stage_cycles = para_block_cycles / 8u;
    const auto adjusted_ur_reads =
        add_ur_read_parking(sched_ur_r0, sched_ur_r1, sched_ur_r2, para_total_cycles);

    auto add_iq = [&](const std::vector<CosSchedEntry>& sched,
                      uint32_t iq_id, const std::string& iq_name,
                      uint32_t code_bits, bool hold_route = false,
                      bool step_addr_inc = false) {
        if (sched.empty()) return;
        IqInstructionFile file;
        file.para_iq_id = iq_id;
        file.para_iq_name = iq_name;
        file.para_width_bits = 41 + code_bits;
        if (!sched_to_embed_slot_block_iq_bytes(sched, code_bits, para_total_cycles,
                                                para_block_cycles, file.para_data,
                                                hold_route, step_addr_inc)) {
            file.para_data = sched_to_iq_bytes(sched, code_bits, hold_route);
        }
        files.push_back(file);
    };

    add_iq(adjusted_ur_reads[0], iq::IQ_ID_UR_RDATA_0, iq::IQ_NAME_UR_RDATA_0, 12, false,
           sched_can_use_step_addr_inc(adjusted_ur_reads[0], para_total_cycles, para_block_cycles));
    add_iq(adjusted_ur_reads[1], iq::IQ_ID_UR_RDATA_1, iq::IQ_NAME_UR_RDATA_1, 12, false, true);
    add_iq(adjusted_ur_reads[2], iq::IQ_ID_UR_RDATA_2, iq::IQ_NAME_UR_RDATA_2, 12, false, false);
    add_iq(sched_ur_w0, iq::IQ_ID_UR_WDATA_0, iq::IQ_NAME_UR_WDATA_0, 28, false, true);
    if (!sched_fpmul.empty()) {
        IqInstructionFile file;
        file.para_iq_id = iq::IQ_ID_FPMUL;
        file.para_iq_name = iq::IQ_NAME_FPMUL;
        file.para_width_bits = 41 + 3;
        if (!sched_to_cos_fpmul_stage_iq_bytes(sched_fpmul, 3, para_total_cycles,
                                                stage_cycles, file.para_data)) {
            file.para_data = sched_to_iq_bytes(sched_fpmul, 3, false);
        }
        files.push_back(file);
    }
    add_iq(sched_fpadd, iq::IQ_ID_FPADD, iq::IQ_NAME_FPADD, 5, false, false);

    add_iq(sched_rtovr_3, 24, "rtovr-3", 7, true, false);
    add_iq(sched_rtovr_4, 24, "rtovr-4", 7, true, false);
    add_iq(sched_rtovr_5, 24, "rtovr-5", 7, true, false);
    add_iq(sched_rtovr_6, 24, "rtovr-6", 7, true, false);
    add_iq(sched_rtovr_35, 24, "rtovr-35", 7, true, false);

    return files;
}

std::vector<uint8_t> CosOpInstructions::serialize() const {
    std::vector<uint8_t> result;
    auto push_u32 = [&](uint32_t v) {
        result.push_back(static_cast<uint8_t>(v & 0xFF));
        result.push_back(static_cast<uint8_t>((v >> 8) & 0xFF));
        result.push_back(static_cast<uint8_t>((v >> 16) & 0xFF));
        result.push_back(static_cast<uint8_t>((v >> 24) & 0xFF));
    };
    push_u32(para_total_steps);
    push_u32(para_total_elements);
    push_u32(para_total_cycles);
    push_u32(para_block_cycles);
    push_u32(para_taylor_terms);
    return result;
}

REGISTER_OP(CosOp);

} // namespace op
} // namespace aiacc
