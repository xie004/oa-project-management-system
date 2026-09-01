import sqlite3
import unittest

from app.services.authority import infer_document_authority
from app.services.data_quality import document_version_group, is_invalid_candidate, normalize_title
from app.services.extractors import bullet_lines, compact_text
from app.services.progress import parse_progress_percent
from app.services.scanner import _parse_ai_items, _validate_ai_items, create_suggestion
from app.services.wiki import (
    _ground_model_citations,
    _logical_version_key,
    _source_type,
    _validate_model_grounding,
)


class DataQualityRulesTest(unittest.TestCase):
    def test_report_progress_formats_are_normalized_to_percent(self):
        self.assertEqual(parse_progress_percent("73/73+"), 100)
        self.assertEqual(parse_progress_percent("完成40/75"), 53)
        self.assertEqual(parse_progress_percent("85%"), 85)
        self.assertEqual(parse_progress_percent(120), 100)
        self.assertEqual(parse_progress_percent("待确认"), 0)

    def test_page_footer_and_repeated_headers_are_removed(self):
        text = "项目周报\n第 5 页 共 5 页\n项目周报\n项目周报\n完成测试环境部署"
        cleaned = compact_text(text)
        self.assertNotIn("第 5 页 共 5 页", cleaned)
        self.assertEqual(cleaned.count("项目周报"), 1)

    def test_candidate_normalization_handles_tense_without_losing_identity(self):
        self.assertEqual(normalize_title("下周数据批量迁移"), normalize_title("数据批量迁移"))
        self.assertEqual(
            normalize_title("FW流程制作验证（第二批表单完成40/75）"),
            normalize_title("FW流程制作验证（第二批表单完成75/75）"),
        )
        self.assertTrue(is_invalid_candidate("第 5 页 共 5 页")[0])

    def test_position_prefix_does_not_pollute_rule_candidate(self):
        self.assertEqual(bullet_lines("[工作表 周报/行 12] 完成测试环境部署"), ["完成测试环境部署"])

    def test_meeting_content_cannot_promote_authority(self):
        result = infer_document_authority(
            {"name": "项目会议纪要.docx", "path": "会议纪要/项目会议纪要.docx", "doc_category": "meeting"},
            "会议讨论了合同、招投标文件和采购需求。",
        )
        self.assertEqual(result["authority_level"], 4)

    def test_version_group_ignores_draft_and_date_suffixes(self):
        left = document_version_group("OA项目采购招标文件初稿（2025122202）.docx")
        right = document_version_group("OA项目采购招标文件拟定稿（2025122401）.docx")
        self.assertEqual(left, right)

    def test_markdown_json_can_be_repaired_locally(self):
        items = _parse_ai_items('```json\n[{"type":"task","title":"完成联调"}]\n```')
        self.assertEqual(items[0]["title"], "完成联调")
        with self.assertRaises(ValueError):
            _validate_ai_items(items, {"task"})

    def test_wiki_source_rules_dedupe_formats_and_keep_weekly_type(self):
        left = _logical_version_key({"id": 1, "name": "项目启动会会议纪要.docx", "doc_category": "meeting"})
        right = _logical_version_key({"id": 2, "name": "项目启动会会议纪要.pdf", "doc_category": "meeting"})
        self.assertEqual(left, right)
        self.assertEqual(
            _source_type({"name": "项目周报（20260824-20260827）.xls", "doc_category": "meeting"}),
            "weekly_report",
        )

    def test_wiki_model_numbers_must_exist_in_selected_sources(self):
        selected = [{"name": "项目合同.docx", "text": "合同金额为1859880元，工期8个月。"}]
        valid = {"summary": "合同金额为1859880元，工期8个月。", "key_points": ["金额为1859880元"]}
        _validate_model_grounding(valid, selected, "overview")
        with self.assertRaises(ValueError):
            _validate_model_grounding(
                {"summary": "合同金额为191万元。", "key_points": ["金额为191万元"]},
                selected,
                "overview",
            )

    def test_wiki_citations_are_reassigned_by_source_overlap(self):
        selected = [
            {"name": "合同.docx", "text": "项目试运行期不少于30日。"},
            {"name": "周报.xls", "text": "档案系统数据迁移正在进行中。"},
        ]
        data = {"key_points": ["档案系统数据迁移正在进行中【S1】"]}
        _ground_model_citations(data, selected, "current_progress")
        self.assertTrue(data["key_points"][0].endswith("【S2】"))


class CandidateAggregationTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE project_entities (
                id INTEGER PRIMARY KEY, entity_type TEXT, record_id INTEGER,
                canonical_title TEXT, normalized_key TEXT, status TEXT,
                merged_into_entity_id INTEGER, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE entity_evidence (
                id INTEGER PRIMARY KEY, entity_id INTEGER, document_id INTEGER,
                suggestion_id INTEGER, evidence_hash TEXT, snippet TEXT,
                locator TEXT, observed_json TEXT, confidence REAL,
                extraction_method TEXT, created_at TEXT,
                UNIQUE(entity_id, evidence_hash)
            );
            CREATE TABLE update_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER,
                suggestion_type TEXT, title TEXT, description TEXT, confidence REAL,
                payload_json TEXT, status TEXT, normalized_title TEXT, fingerprint TEXT,
                candidate_action TEXT, matched_entity_type TEXT, matched_entity_id INTEGER,
                similarity REAL, quality_score REAL, quality_warnings_json TEXT,
                evidence_text TEXT, evidence_locator TEXT, created_at TEXT, applied_at TEXT
            );
            CREATE TABLE suggestion_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT, suggestion_id INTEGER,
                document_id INTEGER, evidence_hash TEXT, evidence_text TEXT,
                locator TEXT, observed_json TEXT, created_at TEXT,
                UNIQUE(suggestion_id, evidence_hash)
            );
            INSERT INTO project_entities VALUES(
                1, 'task', 999, '完全无关的事项', '完全无关的事项', 'active', NULL, '', ''
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_same_candidate_from_two_documents_becomes_one_with_two_sources(self):
        payload = {"title": "完成测试环境部署", "status": "not_started"}
        for document_id in [1, 2]:
            create_suggestion(
                self.conn,
                document_id,
                "task",
                "完成测试环境部署",
                "部署测试环境中的应用和数据库。",
                payload,
                0.82,
                locator=f"文档 {document_id}/段落 3",
                evidence_text="完成测试环境部署",
            )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM update_suggestions").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM suggestion_sources").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
