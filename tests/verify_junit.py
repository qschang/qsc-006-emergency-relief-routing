#!/usr/bin/env python3
import sys, xml.etree.ElementTree as ET

EXPECTED = {
    'test_parallel_edges_and_cut_certificate', 'test_self_loop_is_neutral_to_flow_and_path',
    'test_negative_cost_path_and_lexicographic_tie_break', 'test_unreachable_sink_returns_null_certificates',
    'test_rejects_negative_cycle_atomically', 'test_late_invalid_edge_preserves_sentinel',
    'test_node_order_only_changes_sorted_projection', 'test_flow_cut_values_match_on_disconnected_components',
    'test_zero_capacity_edges_are_ignored', 'test_invalid_boolean_capacity_rejected', 'test_output_has_exact_public_keys',
    'test_invalid_top_level_preserves_absent_destination_and_no_artifacts',
    'test_invalid_cost_preserves_existing_sentinel_byte_for_byte',
    'test_late_invalid_record_does_not_publish_prefix_or_checkpoint',
    'test_released_rejected_released_state_matrix'
}
if len(sys.argv) not in (2,): raise SystemExit(2)
try:
    root = ET.parse(sys.argv[1]).getroot()
    cases = root.findall('.//testcase')
    names = [c.attrib.get('name','') for c in cases]
    if len(cases) != len(EXPECTED) or set(names) != EXPECTED or len(set(names)) != len(names): raise ValueError('unexpected testcase identities')
    for key in ('failures','errors','skipped','disabled'):
        if int(root.attrib.get(key,'0')) != 0: raise ValueError('non-passing junit status')
    if int(root.attrib.get('tests','-1')) != len(EXPECTED): raise ValueError('wrong test count')
except Exception as exc:
    print(f'junit rejected: {exc}', file=sys.stderr); raise SystemExit(1)
