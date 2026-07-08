#include "neg_op_rtovr_inst.h"

namespace aiacc {
namespace op {

uint32_t neg_encode_rtovr_source(uint8_t source_id) {
    return 1u | (static_cast<uint32_t>(source_id & 0x3F) << 1);
}

} // namespace op
} // namespace aiacc

