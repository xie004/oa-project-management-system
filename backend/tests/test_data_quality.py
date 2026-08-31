import sqlite3
import unittest

from app.services.authority import infer_document_authority
from app.services.data_quality import document_version_group, is_invalid_candidate, normalize_title
from app.services.extractors import bullet_lines, compact_text
from app.services.scanner import _parse_ai_items, _validate_ai_items, create_suggestion


class DataQualityRulesTest(unittest.TestCase):
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
