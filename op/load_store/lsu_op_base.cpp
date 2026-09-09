#include "lsu_op_base.h"
#include "../iq/iq_id_defs.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>

namespace aiacc {
namespace lsu {

uint16_t LsuOpBase::compose_ur_addr(uint8_t bank, uint16_t word_addr) {
    return static_cast<uint16_t>(((bank & 0x7u) << 12) | ((word_addr & 0xFFu) << 4));
}

uint16_t LsuOpBase::calc_ldb_loops(uint16_t word_count) const {
    return (word_count > 0) ? static_cast<uint16_t>(word_count - 1) : 0;
}

uint8_t LsuOpBase::calc_ldbaxi_loops(uint16_t word_count) const {
    uint32_t bursts = (word_count + 15) / 16;
    return static_cast<uint8_t>((bursts > 0) ? (bursts - 1) : 0);
}

std::vector<LsuOpBase::BurstSeg> LsuOpBase::bulk_burst_segments(uint16_t N) {
    std::vector<BurstSeg> segs;
    if (N == 0) return segs;
    uint16_t full = static_cast<uint16_t>(N / 16);
    uint16_t rem  = static_cast<uint16_t>(N % 16);
    if (full == 0) {                       // N in [1,15]：单段 N 拍
        segs.push_back({N, 1, 0});
        return segs;
    }
    uint64_t off = 0;
    if (rem == 0 && full >= 4 && (full % 4) == 0) {
        // A terminal group of 4/8/12 full bursts can leave LDBAXI/STBAXI
        // outstanding completion open. Keep the same byte count, but make the
        // final 16 lines two independent 8-beat instructions.
        segs.push_back({16, static_cast<uint16_t>(full - 1), 0});
        off = static_cast<uint64_t>(full - 1) * 256u;
        segs.push_back({8, 1, off});
        segs.push_back({8, 1, off + 8u * 16u});
    } else if (rem == 1) {
        // 末尾 17 拍拆成 9+8，避免出现 brst=0（单拍 burst 会让 LDBAXI 卡住）
        if (full - 1 > 0) {
            segs.push_back({16, static_cast<uint16_t>(full - 1), 0});
            off = static_cast<uint64_t>(full - 1) * 256u;
        }
        segs.push_back({9, 1, off}); off += 9u * 16u;
        segs.push_back({8, 1, off});
    } else {
        segs.push_back({16, full, 0});
        off = static_cast<uint64_t>(full) * 256u;
        if (rem >= 2) segs.push_back({rem, 1, off});
    }
    return segs;
}

uint16_t LsuOpBase::largest_burst_beats(uint16_t word_count) {
    // 取 <=16 且能整除 word_count 的最大拍数，使 (拍数 × burst 数) == word_count，AXI 不多发
    if (word_count == 0) return 1;
    for (uint16_t b = (word_count < 16 ? word_count : 16); b >= 1; --b) {
        if (word_count % b == 0) return b;
    }
    return 1;
}

uint32_t LsuOpBase::total_input_words() const {
    uint32_t total = 0;
    for (auto& [name, port] : desc_.inputs) {
        total += port.word_count;
    }
    return total;
}

uint64_t LsuOpBase::input_gr_base(const std::string& name) const {
    // bulk 模式：HBM 数据连续存放，基址按 word_count*16 精确连续；
    // loops 模式：沿用 (word_count+1)*16 约定（与 loops 下 AXI 寻址对齐）。
    uint64_t offset = 0;
    for (auto& [n, port] : desc_.inputs) {
        if (n == name) return offset;
        offset += seg_stride_bytes(port.word_count);
    }
    return offset;
}

uint64_t LsuOpBase::store_gr_base() const {
    uint64_t offset = 0;
    for (auto& [name, port] : desc_.inputs) {
        offset += seg_stride_bytes(port.word_count);
    }
    return offset;
}

uint64_t LsuOpBase::seg_stride_bytes(uint16_t word_count) const {
    if (desc_.bulk_mode) {
        // bulk：每段按 4KB(256 行) 对齐，避免大 burst 跨 4KB 边界（AXI 非法跨界会回卷）
        uint32_t lines = ((static_cast<uint32_t>(word_count) + 255u) / 256u) * 256u;
        return static_cast<uint64_t>(lines) * 16u;
    }
    // loops：沿用 (word_count+1)*16 约定
    return static_cast<uint64_t>(word_count + 1u) * 16u;
}

op::LsuLoadParams LsuOpBase::build_load_params(uint8_t bank, uint16_t word_offset,
                                                uint16_t word_count, uint64_t gr_base) const {
    op::LsuLoadParams p{};
    p.para_ur_addr = compose_ur_addr(bank, word_offset);
    p.para_line_byte_cnt = 0x0F;            // 16 字节/行
    p.para_line_stride = 1;
    p.para_smc_id_en = desc_.smc_id_en;
    p.para_gr_base_addr = gr_base;
    p.para_gr_intlv_addr = desc_.gr_intlv_addr;
    p.para_ldb_vld = true;
    p.para_ldbaxi_vld = true;

    if (desc_.bulk_mode) {
        // LDB：一条指令写 word_count 行（ur_wr_line_cnt），不用外壳 loops
        p.para_ur_wr_line_cnt = static_cast<uint8_t>(word_count & 0xFF);
        p.para_ldb_loops = 0;
        p.para_ldb_inc = 0x0010;
        // LDBAXI 主体：16 拍/burst（brst=15），共 floor(N/16) 个 burst，地址每次 +0x100。
        // 余数 N%16 由 generate_load_iq_files 追加一条 LDBAXI 处理（见那里）。
        uint16_t full_bursts = static_cast<uint16_t>(word_count / 16);
        uint16_t rem = static_cast<uint16_t>(word_count % 16);
        if (full_bursts >= 1) {
            p.para_brst = 0x0F;                                   // 16 拍
            p.para_ldbaxi_loops = static_cast<uint16_t>(full_bursts - 1);
            p.para_ldbaxi_inc = 0x0100;                           // 每 burst +256B(16 行)
        } else {
            // N<16：主体即余数，一条 N 拍 burst
            p.para_brst = static_cast<uint8_t>((rem - 1) & 0x0F);
            p.para_ldbaxi_loops = 0;
            p.para_ldbaxi_inc = 0x0100;
        }
    } else {
        // loops 模式：每条 LDB 写 1 行，外壳 loops 重复 word_count 次
        p.para_ur_wr_line_cnt = 1;
        p.para_ldb_loops = calc_ldb_loops(word_count);
        p.para_ldbaxi_loops = calc_ldbaxi_loops(word_count);
        p.para_brst = 0x0F;
        p.para_ldb_inc = 0x0010;
        p.para_ldbaxi_inc = 0x0100;
    }
    return p;
}

op::LsuStoreParams LsuOpBase::build_store_params(uint8_t bank, uint16_t word_offset,
                                                  uint16_t word_count, uint64_t gr_base) const {
    op::LsuStoreParams p{};
    p.para_ur_addr = compose_ur_addr(bank, word_offset);
    p.para_line_byte_cnt = 0x0F;
    p.para_line_stride = 1;
    p.para_smc_id_en = desc_.smc_id_en;
    p.para_gr_base_addr = gr_base;
    p.para_gr_intlv_addr = desc_.gr_intlv_addr;
    p.para_stb_vld = true;
    p.para_stbaxi_vld = true;
    p.para_tid = 2;

    if (desc_.bulk_mode) {
        p.para_ur_rd_line_cnt = static_cast<uint8_t>(word_count & 0xFF);
        p.para_stb_loops = 0;
        p.para_stb_inc = 0x0010;
        uint16_t full_bursts = static_cast<uint16_t>(word_count / 16);
        uint16_t rem = static_cast<uint16_t>(word_count % 16);
        if (full_bursts >= 1) {
            p.para_brst = 0x0F;
            p.para_stbaxi_loops = static_cast<uint16_t>(full_bursts - 1);
            p.para_stbaxi_inc = 0x0100;
        } else {
            p.para_brst = static_cast<uint8_t>((rem - 1) & 0x0F);
            p.para_stbaxi_loops = 0;
            p.para_stbaxi_inc = 0x0100;
        }
    } else {
        p.para_ur_rd_line_cnt = 1;
        p.para_stb_loops = calc_ldb_loops(word_count);
        p.para_stbaxi_loops = calc_ldbaxi_loops(word_count);
        p.para_brst = 0x0F;
        p.para_stb_inc = 0x0010;
        p.para_stbaxi_inc = 0x0100;
    }
    return p;
}

std::vector<op::IqInstructionFile> LsuOpBase::generate_load_iq_files() const {
    std::vector<op::IqInstructionFile> all;
    for (auto& [name, port] : desc_.inputs) {
        uint64_t gr_base = input_gr_base(name);
        // Two consecutive one-beat LDBAXI inputs are not reliable: the later
        // input can complete without reaching UR. Bulk input regions are 4KB
        // aligned, so loading one zero-padded guard line is safe while the
        // logical shape, compute count, metadata and golden remain unchanged.
        const uint16_t transfer_words =
            (desc_.bulk_mode && port.word_count == 1) ? 2 : port.word_count;
        auto p = build_load_params(port.bank, port.word_offset, transfer_words, gr_base);

        if (!desc_.bulk_mode) {
            auto files = op::emit_load_iq_files(p);
            for (auto& f : files) all.push_back(std::move(f));
            continue;
        }
        // bulk：LDB 一条（取 LDB），LDBAXI 按 burst 分段逐段发（每段取 LDBAXI）
        {
            auto lf = op::emit_load_iq_files(p);
            for (auto& f : lf) if (f.para_iq_id == iq::IQ_ID_LDB) all.push_back(std::move(f));
        }
        for (auto& seg : bulk_burst_segments(transfer_words)) {
            auto ps = p;
            ps.para_brst = static_cast<uint8_t>((seg.beats - 1) & 0x0F);
            ps.para_ldbaxi_loops = static_cast<uint16_t>(seg.num - 1);
            ps.para_ldbaxi_inc = 0x0100;                 // 16 拍主体段每 burst +256B
            ps.para_gr_base_addr = gr_base + seg.byte_off;
            auto sf = op::emit_load_iq_files(ps);
            for (auto& f : sf) if (f.para_iq_id == iq::IQ_ID_LDBAXI) all.push_back(std::move(f));
        }
    }
    return all;
}

std::vector<op::IqInstructionFile> LsuOpBase::generate_store_iq_files() const {
    std::vector<op::IqInstructionFile> all;
    uint64_t gr_base = store_gr_base();
    auto p = build_store_params(desc_.output.bank, desc_.output.word_offset,
                                desc_.output.word_count, gr_base);

    if (!desc_.bulk_mode) {
        return op::emit_store_iq_files(p);
    }
    {
        auto sf = op::emit_store_iq_files(p);
        for (auto& f : sf) if (f.para_iq_id == iq::IQ_ID_STB) all.push_back(std::move(f));
    }
    for (auto& seg : bulk_burst_segments(desc_.output.word_count)) {
        auto ps = p;
        ps.para_brst = static_cast<uint8_t>((seg.beats - 1) & 0x0F);
        ps.para_stbaxi_loops = static_cast<uint16_t>(seg.num - 1);
        ps.para_stbaxi_inc = 0x0100;
        ps.para_gr_base_addr = gr_base + seg.byte_off;
        auto sf = op::emit_store_iq_files(ps);
        for (auto& f : sf) if (f.para_iq_id == iq::IQ_ID_STBAXI) all.push_back(std::move(f));
    }
    return all;
}

void LsuOpBase::fill_input_port_data(const std::string& /*name*/,
                                     const PortInfo& port,
                                     uint8_t /*smc_id*/,
                                     std::mt19937& rng,
                                     std::vector<uint8_t>& out) const {
    // 跨数量级随机：尾数 [1,2) × 10^E（E∈[-20,20]）× 随机符号，仅有限值。
    // 范围约 ±1e-20 ~ ±2e20，覆盖正负与极大/极小，避免直接生成 inf/NaN/非规格化数。
    std::uniform_real_distribution<float> mant(1.0f, 2.0f);
    std::uniform_int_distribution<int> expo(-20, 20);
    std::uniform_int_distribution<int> sgn(0, 1);
    auto gen_val = [&]() -> float {
        float v = mant(rng) * std::pow(10.0f, static_cast<float>(expo(rng)));
        return sgn(rng) ? -v : v;
    };

    out.assign(static_cast<size_t>(port.word_count) * 16u, 0);
    for (uint32_t i = 0; i < static_cast<uint32_t>(out.size()) / 4; ++i) {
        float val = gen_val();
        uint32_t bits;
        std::memcpy(&bits, &val, 4);
        const uint64_t byte_off = static_cast<uint64_t>(i) * 4u;
        out[byte_off + 0] = static_cast<uint8_t>(bits & 0xFF);
        out[byte_off + 1] = static_cast<uint8_t>((bits >> 8) & 0xFF);
        out[byte_off + 2] = static_cast<uint8_t>((bits >> 16) & 0xFF);
        out[byte_off + 3] = static_cast<uint8_t>((bits >> 24) & 0xFF);
    }
}

void LsuOpBase::write_extra_meta_json(std::ostream& /*os*/) const {}

void LsuOpBase::generate_hbm_data(uint8_t smc_id, uint32_t seed,
                                   std::vector<uint8_t>& hbm_bytes) const {
    std::mt19937 rng(seed + smc_id * 1000);

    uint64_t total = store_gr_base() + static_cast<uint64_t>(desc_.output.word_count) * 16u;
    for (auto& [name, port] : desc_.inputs) {
        total = std::max(total, input_gr_base(name) + static_cast<uint64_t>(port.word_count) * 16u);
    }
    hbm_bytes.assign(static_cast<size_t>(total), 0);

    for (auto& [name, port] : desc_.inputs) {
        std::vector<uint8_t> port_data;
        fill_input_port_data(name, port, smc_id, rng, port_data);
        const uint64_t base = input_gr_base(name);
        const uint64_t end = base + port_data.size();
        if (end > hbm_bytes.size()) {
            hbm_bytes.resize(static_cast<size_t>(end), 0);
        }
        std::copy(port_data.begin(), port_data.end(), hbm_bytes.begin() + base);
    }
}

static void mkdir_p(const std::string& path) {
    (void)system((std::string("mkdir -p \"") + path + "\"").c_str());
}

static void write_hex_file(const std::string& path, const std::vector<uint8_t>& data,
                           uint32_t line_width_bytes) {
    std::ofstream ofs(path);
    if (!ofs) return;
    uint32_t lines = static_cast<uint32_t>(data.size()) / line_width_bytes;
    for (uint32_t l = 0; l < lines; ++l) {
        for (int b = static_cast<int>(line_width_bytes) - 1; b >= 0; --b) {
            uint8_t byte = data[l * line_width_bytes + b];
            ofs << std::hex << std::setfill('0') << std::setw(2)
                << static_cast<unsigned>(byte);
        }
        ofs << "\n";
    }
}

static void write_zero_bank(const std::string& path, uint32_t lines) {
    std::ofstream ofs(path);
    if (!ofs) return;
    std::string zero_line(32, '0');
    for (uint32_t i = 0; i < lines; ++i) {
        ofs << zero_line << "\n";
    }
}

static std::string iq_filename_from_name(uint32_t /*id*/, const std::string& raw_name) {
    if (raw_name == "ldbaxi") return "ldb_axi";
    if (raw_name == "stbaxi") return "stb_axi";
    if (raw_name == "ur_rdata_0") return "ur-r0";
    if (raw_name == "ur_rdata_1") return "ur-r1";
    if (raw_name == "ur_rdata_2") return "ur-r2";
    if (raw_name == "ur_rdata_3") return "ur-r3";
    if (raw_name == "ur_wdata_0") return "ur-w0";
    if (raw_name == "ur_wdata_1") return "ur-w1";
    if (raw_name == "ur_wdata_2") return "ur-w2";
    if (raw_name == "ur_wdata_3") return "ur-w3";
    return raw_name;
}
// 把一组 IQ 文件按队列名合并后，写到指定目录下的 iq_<name>.hex（每条指令一行）。
// v0.4.20 multi_inst_stage：指令按 stage 分目录放在 case_root/smi_iq_inst/stage_<k>/，
// 是 smc_* 的同级（case 级），不再放进 smc_0/。
static void write_iq_files_to_dir(const std::string& dir,
                                  const std::vector<op::IqInstructionFile>& files) {
    struct IqFileEntry {
        std::vector<uint8_t> data;
        uint32_t width_bits{0};
    };
    std::map<std::string, IqFileEntry> file_map;
    for (auto& f : files) {
        std::string fname = iq_filename_from_name(f.para_iq_id, f.para_iq_name);
        auto& entry = file_map[fname];
        entry.data.insert(entry.data.end(), f.para_data.begin(), f.para_data.end());
        if (entry.width_bits == 0) entry.width_bits = f.para_width_bits;
    }
    for (auto& [fname, entry] : file_map) {
        std::string path = dir + "/iq_" + fname + ".hex";
        std::ofstream ofs(path);
        if (!ofs) continue;
        uint32_t inst_bytes = (entry.width_bits + 7) / 8;
        if (inst_bytes == 0) inst_bytes = static_cast<uint32_t>(entry.data.size());
        uint32_t num_insts = static_cast<uint32_t>(entry.data.size()) / inst_bytes;
        for (uint32_t n = 0; n < num_insts; ++n) {
            for (uint32_t b = 0; b < inst_bytes; ++b) {
                ofs << std::hex << std::setfill('0') << std::setw(2)
                    << static_cast<unsigned>(entry.data[n * inst_bytes + b]);
            }
            ofs << "\n";
        }
    }
}

bool LsuOpBase::assemble_full_case() const {
    const std::string case_root = cfg_.case_root;

    auto load_iqs = generate_load_iq_files();
    auto compute_iqs = generate_compute_iq_files();
    auto store_iqs = generate_store_iq_files();

    // 4.20/4.21 multi-stage TB consumes case-level stage directories.
    // Do not emit an empty middle stage: the TB waits for each non-last stage
    // to raise done/intr, so a zero-IQ stage would timeout.
    const std::string smi_iq_root = case_root + "/smi_iq_inst";
    (void)system((std::string("rm -rf \"") + smi_iq_root + "\"").c_str());
    mkdir_p(smi_iq_root);
    std::vector<std::pair<std::string, const std::vector<op::IqInstructionFile>*>> stages;
    stages.push_back({"load", &load_iqs});
    if (!compute_iqs.empty()) {
        stages.push_back({"compute", &compute_iqs});
    }
    stages.push_back({"store", &store_iqs});
    for (size_t i = 0; i < stages.size(); ++i) {
        const std::string stage_dir = smi_iq_root + "/stage_" + std::to_string(i);
        mkdir_p(stage_dir);
        write_iq_files_to_dir(stage_dir, *stages[i].second);
    }
    {
        std::ofstream sf(smi_iq_root + "/stages.md");
        if (sf) {
            sf << "# " << cfg_.case_name << " stages\n\n";
            sf << "- stage_count: " << stages.size() << "\n\n";
            for (size_t i = 0; i < stages.size(); ++i) {
                sf << "- stage_" << i << ": " << stages[i].first << "\n";
            }
        }
    }

    // 4.19 compatibility: load+compute+store are also merged into smc_0/iq_*.hex.

    for (uint8_t smc = 0; smc < desc_.num_smc; ++smc) {
        std::string smc_dir = case_root + "/smc_" + std::to_string(smc);
        mkdir_p(smc_dir + "/ur_data");
        mkdir_p(smc_dir + "/output_bank");

        for (int b = 0; b < 8; ++b) {
            write_zero_bank(smc_dir + "/ur_data/bank" + std::to_string(b) + ".hex", 256);
        }

        if (smc == 0) {
            struct IqFileEntry { std::vector<uint8_t> data; uint32_t width_bits{0}; };
            std::map<std::string, IqFileEntry> file_map;
            auto collect_iq = [&](const std::vector<op::IqInstructionFile>& files) {
                for (auto& f : files) {
                    std::string fname = iq_filename_from_name(f.para_iq_id, f.para_iq_name);
                    auto& entry = file_map[fname];
                    entry.data.insert(entry.data.end(), f.para_data.begin(), f.para_data.end());
                    if (entry.width_bits == 0) entry.width_bits = f.para_width_bits;
                }
            };
            collect_iq(load_iqs);
            collect_iq(compute_iqs);
            collect_iq(store_iqs);

            for (auto& [fname, entry] : file_map) {
                std::string path = smc_dir + "/iq_" + fname + ".hex";
                std::ofstream ofs(path);
                if (!ofs) continue;
                uint32_t inst_bytes = (entry.width_bits + 7) / 8;
                if (inst_bytes == 0) inst_bytes = static_cast<uint32_t>(entry.data.size());
                uint32_t num_insts = static_cast<uint32_t>(entry.data.size()) / inst_bytes;
                for (uint32_t n = 0; n < num_insts; ++n) {
                    for (uint32_t b = 0; b < inst_bytes; ++b)
                        ofs << std::hex << std::setfill('0') << std::setw(2)
                            << static_cast<unsigned>(entry.data[n * inst_bytes + b]);
                    ofs << "\n";
                }
            }
        }

        std::vector<uint8_t> hbm_data;
        generate_hbm_data(smc, cfg_.seed_base, hbm_data);

        std::string hbm_path = cfg_.hbm_init_dir + "/memory_init" + std::to_string(smc + 1) + ".hex";
        write_hex_file(hbm_path, hbm_data, 16);

        // 按与 HBM 写入相同的布局提取各输入段。
        std::map<std::string, std::vector<uint8_t>> input_datas;
        for (auto& [name, port] : desc_.inputs) {
            uint64_t base = input_gr_base(name);
            uint32_t data_bytes = port.word_count * 16;
            input_datas[name] = std::vector<uint8_t>(
                hbm_data.begin() + base,
                hbm_data.begin() + base + data_bytes);
        }

        std::vector<uint8_t> golden;
        generate_golden_for_smc(smc, input_datas, golden);

        const uint32_t ur_bank_depth = 256;
        if (golden.size() / 16 < ur_bank_depth) {
            golden.resize(ur_bank_depth * 16, 0);
        }

        write_hex_file(smc_dir + "/output_bank/bank" + std::to_string(desc_.output.bank) + "_final.hex",
                       golden, 16);
    }

    // ===== 落一份 meta_v3.json，供 DDR 真比对脚本(verify_ddr_v3.py)使用 =====
    {
        std::ofstream mf(case_root + "/meta_v3.json");
        if (mf) {
            mf << "{\n";
            mf << "  \"op\": \"" << get_name() << "\",\n";
            mf << "  \"num_smc\": " << static_cast<int>(desc_.num_smc) << ",\n";
            mf << "  \"out_bank\": " << static_cast<int>(desc_.output.bank) << ",\n";
            mf << "  \"out_words\": " << desc_.output.word_count << ",\n";
            mf << "  \"store_byte_base\": " << store_gr_base() << ",\n";
            mf << "  \"word_bytes\": 16,\n";
            write_extra_meta_json(mf);
            mf << "  \"inputs\": [\n";
            {
                size_t i = 0, ni = desc_.inputs.size();
                for (auto& [name, port] : desc_.inputs) {
                    mf << "    {\"name\": \"" << name << "\", \"byte_base\": " << input_gr_base(name)
                       << ", \"words\": " << port.word_count
                       << ", \"word_offset\": " << port.word_offset << "}"
                       << (++i < ni ? "," : "") << "\n";
                }
            }
            mf << "  ]\n";
            mf << "}\n";
        }
    }

    std::cout << "[LsuOpBase] Case assembled: " << case_root << " ("
              << static_cast<int>(desc_.num_smc) << " SMCs)" << std::endl;
    return true;
}

} // namespace lsu
} // namespace aiacc
