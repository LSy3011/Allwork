#ifndef MUL_SCALAR_UR_CODE_EXTRACTOR_H
#define MUL_SCALAR_UR_CODE_EXTRACTOR_H

#include <cstdint>
#include <stdexcept>

#include "../../iq/modules/ur_iq.h"

namespace aiacc {
namespace op {

inline uint32_t extract_ur_read_port_code(const iq::UrCru& cru, int port) {
    if (port < 0 || port >= 4) {
        throw std::out_of_range("extract_ur_read_port_code: port must be in [0, 3]");
    }
    const uint32_t bank = cru.para_raddr[port].bank & 0x7u;
    const uint32_t addr = cru.para_raddr[port].addr_word & 0xFFu;
    const uint32_t re = cru.para_re[port] ? 1u : 0u;

    uint32_t code = 0;
    code |= re;
    code |= (bank << 1);
    code |= (addr << 4);
    return code;
}

inline uint32_t extract_ur_read_port0_code(const iq::UrCru& cru) {
    return extract_ur_read_port_code(cru, 0);
}

inline uint32_t extract_ur_read_port1_code(const iq::UrCru& cru) {
    return extract_ur_read_port_code(cru, 1);
}

inline uint32_t extract_ur_write_port_code(const iq::UrCru& cru, int port) {
    if (port < 0 || port >= 4) {
        throw std::out_of_range("extract_ur_write_port_code: port must be in [0, 3]");
    }
    const uint32_t bank = cru.para_waddr[port].bank & 0x7u;
    const uint32_t addr = cru.para_waddr[port].addr_word & 0xFFu;
    const uint32_t we = cru.para_we[port] ? 1u : 0u;
    const uint32_t wbe = cru.para_wbe[port] & 0xFFFFu;

    uint32_t code = 0;
    code |= we;
    code |= (bank << 1);
    code |= (addr << 4);
    code |= (wbe << 12);
    return code;
}

inline uint32_t extract_ur_write_port0_code(const iq::UrCru& cru) {
    return extract_ur_write_port_code(cru, 0);
}

} // namespace op
} // namespace aiacc

#endif // MUL_SCALAR_UR_CODE_EXTRACTOR_H
