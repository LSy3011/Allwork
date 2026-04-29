from .dependency_axioms import DependencyAxioms
from .resource_axioms import ResourceAxioms
from .physical_axioms import PhysicalAxioms
from .special_axioms import SpecialAxioms

# 统一导出
add_latency_axiom = DependencyAxioms.add_latency_axiom
add_dependency_axiom = DependencyAxioms.add_dependency_axiom

add_resource_constraint = ResourceAxioms.add_resource_constraint
add_modulo_constraint = ResourceAxioms.add_modulo_constraint
add_bank_conflict_axiom = ResourceAxioms.add_bank_conflict_axiom
add_spill_reload_mutex_axiom = ResourceAxioms.add_spill_reload_mutex_axiom

add_rtovr_alignment_axiom = PhysicalAxioms.add_rtovr_alignment_axiom
add_side_effect_phase_axiom = PhysicalAxioms.add_side_effect_phase_axiom
add_anti_overwrite_axiom = PhysicalAxioms.add_anti_overwrite_axiom
add_shadow_binding_axiom = PhysicalAxioms.add_shadow_binding_axiom
add_physical_binding_axiom = PhysicalAxioms.add_physical_binding_axiom
add_rtovr_crossbar_axiom = PhysicalAxioms.add_rtovr_crossbar_axiom
add_iq_latch_protect_axiom = PhysicalAxioms.add_iq_latch_protect_axiom
add_alu_liveness_axiom = PhysicalAxioms.add_alu_liveness_axiom
add_iq_alignment_axiom = PhysicalAxioms.add_iq_alignment_axiom

__all__ = [
    "DependencyAxioms",
    "ResourceAxioms",
    "PhysicalAxioms",
    "SpecialAxioms",
    "add_latency_axiom",
    "add_dependency_axiom",
    "add_resource_constraint",
    "add_modulo_constraint",
    "add_bank_conflict_axiom",
    "add_spill_reload_mutex_axiom",
    "add_rtovr_alignment_axiom",
    "add_side_effect_phase_axiom",
    "add_anti_overwrite_axiom",
    "add_shadow_binding_axiom",
    "add_physical_binding_axiom",
    "add_rtovr_crossbar_axiom",
    "add_iq_latch_protect_axiom",
    "add_alu_liveness_axiom",
    "add_iq_alignment_axiom",
]
