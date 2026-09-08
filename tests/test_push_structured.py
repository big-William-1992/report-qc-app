# -*- coding: utf-8 -*-
"""推送接口字段化解析单测：影像描述/影像诊断独立字段 + 别名兼容。"""

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.routes.route_push import PushReportJSON, _parse_xml_report


class TestPushStructured(unittest.TestCase):
    def test_json_field_model(self):
        d = PushReportJSON.from_payload({
            "patient": "张三", "gender": "男", "age": "45",
            "modality": "CT", "applied_site": "胸部",
            "findings_desc": "双肺纹理清晰。",
            "diagnosis": "胸部 CT 未见异常。",
        }).model_dump()
        self.assertEqual(d["findings_desc"], "双肺纹理清晰。")
        self.assertEqual(d["diagnosis"], "胸部 CT 未见异常。")
        self.assertEqual(d["patient"], "张三")

    def test_json_aliases(self):
        d = PushReportJSON.from_payload({
            "description": "所见描述内容",
            "impression": "诊断结论内容",
        }).model_dump()
        self.assertEqual(d["findings_desc"], "所见描述内容")
        self.assertEqual(d["diagnosis"], "诊断结论内容")

        d2 = PushReportJSON.from_payload({
            "findings": "所见",
            "conclusion": "结论",
        }).model_dump()
        self.assertEqual(d2["findings_desc"], "所见")
        self.assertEqual(d2["diagnosis"], "结论")

    def test_xml_flat_structured(self):
        xml = ("<Report>"
               "<PatientName>李四</PatientName><Gender>女</Gender><Age>52</Age>"
               "<Modality>MR</Modality><BodyPart>头颅</BodyPart>"
               "<FindingsDescription>斑片状长T1长T2信号灶。</FindingsDescription>"
               "<Diagnosis>腔隙性脑梗塞可能。</Diagnosis>"
               "</Report>")
        d = _parse_xml_report(xml)
        self.assertEqual(d["findings_desc"], "斑片状长T1长T2信号灶。")
        self.assertEqual(d["diagnosis"], "腔隙性脑梗塞可能。")
        self.assertEqual(d["patient"], "李四")
        self.assertEqual(d["applied_site"], "头颅")

    def test_xml_nested_structured(self):
        xml = ("<Report><Header><PatientName>王五</PatientName></Header>"
               "<Body><FindingsDescription>腰椎退行性改变。</FindingsDescription>"
               "<Diagnosis>腰椎骨质增生。</Diagnosis></Body></Report>")
        d = _parse_xml_report(xml)
        self.assertEqual(d["findings_desc"], "腰椎退行性改变。")
        self.assertEqual(d["diagnosis"], "腰椎骨质增生。")

    def test_xml_old_flat_report_text(self):
        xml = ("<Report><report_text>影像描述：双肺纹理清晰。\n影像诊断：未见异常。"
               "</report_text><patient>赵六</patient></Report>")
        d = _parse_xml_report(xml)
        self.assertIn("双肺纹理清晰", d["report_text"])
        self.assertEqual(d["findings_desc"], "")


if __name__ == "__main__":
    unittest.main()
