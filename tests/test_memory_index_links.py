"""Flat auto-memory indexes must not produce false orphan advice."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/token-optimizer/scripts'))
import measure


@pytest.mark.parametrize('prefix', ['', '# Memory\n', '## Notes\n'])
def test_linked_topic_is_not_an_orphan(tmp_path, prefix):
    index = tmp_path / 'MEMORY.md'
    index.write_text(prefix + '- [Topic](topic.md) - useful note\n')
    (tmp_path / 'topic.md').write_text('details\n')
    parsed = measure._mr_parse_memory_index(index)
    assert len(parsed['links_all']) == 1
    assert measure._mr_detect_orphans(parsed['links_all'], measure._mr_scan_topic_files(tmp_path)) == []
    assert len(parsed['entries']) == (1 if prefix.startswith('##') else 0)
    assert parsed['links_all'][0]['entry_idx'] == (0 if prefix.startswith('##') else None)


def test_flat_and_section_links_keep_positions_without_double_counting(tmp_path):
    index = tmp_path / 'MEMORY.md'
    index.write_text('- [Flat](flat.md)\n## Notes\n- [Section](section.md)\n# New index\n- [Last](last.md)\n')
    parsed = measure._mr_parse_memory_index(index)
    assert [(link['target'], link['line'], link['entry_idx']) for link in parsed['links_all']] == [
        ('flat.md', 0, None), ('section.md', 2, 0), ('last.md', 4, None)]
    assert len(parsed['entries']) == 1
    assert len(parsed['entries'][0]['links']) == 1


def test_flat_broken_link_still_reports_real_orphan(tmp_path):
    index = tmp_path / 'MEMORY.md'
    index.write_text('- [Missing](missing.md)\n')
    parsed = measure._mr_parse_memory_index(index)
    findings = measure._mr_detect_orphans(parsed['links_all'], ['unused.md'])
    assert [finding['category'] for finding in findings] == ['broken_link', 'orphan']
    assert findings[0]['line'] == 0
    assert findings[1]['file'] == 'unused.md'


def test_memory_review_accepts_flat_index_end_to_end(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    memory = home / 'project' / 'memory'
    memory.mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setattr(measure, 'measure_components', lambda: {})
    (memory / 'MEMORY.md').write_text('- [Topic](topic.md)\n')
    (memory / 'topic.md').write_text('useful details\n')
    result = measure.memory_review(project_dir=str(memory.parent), as_json=True)
    assert 'error' not in result
    assert not any(finding['category'] in ('orphan', 'broken_link') for finding in result['findings'])
