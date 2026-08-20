"""
engine/knowledge_tree.py
درخت رسمی دانش سازمانی + navigation helpers.

منبع: `references/knowledge-tree.md` از مهارت `organizational-knowledge-skill`.
ساختار ۴ سطحی دارد. AI اجازهٔ اختراع، تغییر نام، ادغام، جابجایی، یا ساخت
نود جایگزین ندارد — این درخت تنها منبع حقیقت است.

ساختار ذخیرهسازی در DB: `tree_path_json` = لیست JSON از نام نودها، مثلاً
`["MAPNA Development", "HSE Management", "Safety"]`.
"""

from __future__ import annotations


# ══════════════════════════════════════════════════════════════════════════════
# درخت رسمی (دیکشنری تو در تو — برگ‌ها = {})
# ══════════════════════════════════════════════════════════════════════════════

KNOWLEDGE_TREE: dict[str, dict] = {
    "MAPNA Development": {
        "HSE Management": {
            "Safety, Health and Environmental Risk Analysis": {},
            "Health, Safety and Environment": {
                "Occupational Health and Wellness": {},
                "Safety": {},
                "Environment": {},
            },
            "Emergency and Crisis Management": {},
            "HSE Policy and Objective Setting": {},
        },
        "Legal Affairs Management": {
            "Management of Employer Contractual Obligations": {},
            "Property and Ownership Rights": {},
            "Employer Contract Amendments": {},
            "Management of Employer Contract Execution": {},
            "Intellectual Property Rights": {},
            "Management of Employer Contractual Claims and Disputes": {},
            "International Contract Law": {},
            "Domestic Contract Law": {},
        },
        "Execution Management and Supervision": {
            "General Execution": {},
            "Site Handover and Site Mobilization": {},
            "Storage": {},
            "Commissioning, Trial Operation and Performance Testing": {},
            "Civil Works": {},
            "Field Engineering": {},
            "Installation and Pre-Commissioning": {},
        },
        "Design and Engineering": {
            "Civil Engineering": {
                "Underground and Above-Ground Tanks, Pipelines and Gravity Lines": {},
                "Structural Architecture": {},
                "Site Mobilization": {},
                "Site Development and Road Construction": {},
                "Concrete and Steel Structure Execution": {},
                "Equipment Layout, Flooring and Equipment Installation": {},
                "Site Handover, Surveying and Geotechnical Engineering": {},
                "Site Grading, Piling, Excavation and Foundation": {},
                "Rack, Sleeper and Trench": {},
            },
            "Engineering Services": {},
            "Product Development": {
                "Technology": {},
                "Innovation": {},
            },
            "Electrical Engineering": {
                "Protection and Metering": {},
                "Buswork Equipment": {},
                "Electrical Installations": {},
                "Cables, Busducts, Cable Trays, Conduits and Cable Routing": {},
                "Transformers": {},
                "Earthing, Lightning Protection and Surge Arresters": {},
                "Electrical Panels": {},
                "Electrical Heat Tracing, Cathodic Protection and Firestop": {},
                "Diesel Generator Systems and Accessories": {},
                "Motors, Drives and Motor Starters": {},
                "High-Voltage Switchgear": {},
                "Generators, Excitation and Starting Systems and Accessories": {},
                "Batteries, Battery Chargers, Inverters, DC/AC UPS and Power Factor Correction Equipment": {},
            },
            "Energy Efficiency": {
                "Fuel Consumption Optimization": {},
                "Electrical Energy Efficiency": {},
                "Environmental Engineering": {},
                "Water Efficiency": {},
                "Energy-Efficient Buildings": {},
            },
            "Power Transmission and Distribution": {
                "Grid Smartification": {},
                "Customer Domain (Consumers)": {},
                "Microgrids and Distributed Energy Resources": {},
                "Transmission and Sub-Transmission": {},
                "Distribution": {},
            },
            "Mechanical Engineering": {
                "System and Piping Layout": {},
                "Turbine / Generator": {},
                "Common Utilities": {},
                "Boiler": {},
                "Cooling": {},
            },
            "Process Engineering": {
                "Boiler and Cooling Chemical Control": {},
                "Painting and Coating": {},
                "Wastewater Collection and Treatment Systems": {},
                "Water and Condensate Treatment, Distribution and Transfer Systems": {},
            },
            "Control and Instrumentation Engineering": {
                "Instrumentation Equipment": {},
                "SCADA and Telecommunication Systems": {},
                "Surveillance and Security Systems (CCTV & ACCESS CONTROL)": {},
                "Fire Detection and Firefighting Systems": {},
                "Control, Monitoring and EMS Systems": {},
                "Industrial Network Systems (LAN & IP-Based Telephone)": {},
            },
        },
        "System Infrastructure Management": {
            "Strategy Formulation and Development": {},
            "Information Security": {},
            "Network Management": {},
            "Information Systems Design and Development": {},
            "Web Design": {},
            "Information Systems / Software Support": {},
            "Information Systems Architecture": {},
            "Business Intelligence (BI)": {},
        },
        "Human Resources and Support Management": {
            "Development and Empowerment Process": {
                "Training and Empowerment": {},
                "Development and Learning": {},
                "Talent Management": {},
                "Performance Management": {},
                "Succession Planning": {},
                "Mentoring and Coaching": {},
            },
            "Human Resources Policy and Strategy Development": {},
            "Employee Communication Process": {
                "Employee Awareness": {},
                "Employee Experience": {},
                "Attitude Measurement": {
                    "HRIS": {},
                },
            },
            "Organizational Capability Development Process": {
                "Organizational Structure Design": {},
                "Organizational Learning": {},
                "Organizational Culture": {},
                "Leadership": {},
                "Competency Analysis and Development": {},
            },
            "Human Resources Process Infrastructure and Resource Planning and Management": {},
            "Employee Motivation and Retention Process": {
                "Comprehensive Human Resources Health": {},
                "Work and Life Relations": {},
                "Career Development": {},
                "Rewards": {},
                "Compensation and Benefits": {},
            },
            "Human Resources Provision Process": {
                "Recruitment, Hiring and Appointment": {},
                "Job Grading Analysis": {},
                "Human Resources Planning": {},
                "Employee Grading Analysis": {},
            },
        },
        "Customer, Marketing and Sales Management": {
            "Strategic Market and Customer Analysis and Related Strategy Development": {},
            "Customer Surveys During Tendering": {},
            "Customer Relationship Management": {},
            "Brand Management": {},
            "Employer Contracting": {},
            "Public Relations Management": {},
            "Preliminary Feasibility Study": {},
            "Sales Performance Indicator Policy, Formulation and Evaluation": {},
            "Suggestion System": {},
            "Employer Tendering and Contracting": {},
        },
        "Supply Management": {
            "Commercial Services": {
                "Insurance": {},
                "Banking Operations": {},
                "Customs and Clearance Affairs": {},
                "Transportation": {},
            },
            "Installation and Execution Contracts": {
                "BOP Power Plant System Installation and Commissioning Contracts": {},
                "Main Power Plant Equipment Installation and Commissioning Contracts": {},
                "Supply, Installation and Commissioning Contracts for Substations and Transmission Lines (TDE)": {},
                "Power Plant Commissioning and Temporary Operation Contracts": {},
            },
            "Supply Coordination": {
                "Supplier Evaluation and Analysis": {},
                "Procurement Package Follow-up": {},
                "Agile Procurement and Auctions": {},
            },
            "Mechanical Contracts": {
                "Main Boiler Supply Contracts": {},
                "Cooling Contracts": {},
                "Water Sector Contracts": {},
                "Turbine Contracts": {},
                "BOP and Firefighting Contracts": {},
            },
            "Cost Engineering": {
                "Contractors' Contractual Claims": {},
                "Overtime and New Rates": {},
                "Cost Estimation": {},
            },
            "Tendering and Contracting": {
                "Electrical and Instrumentation Tenders": {},
                "Mechanical Tenders": {},
                "Substation and Transmission Line Tenders": {},
            },
            "Consulting, Design and Civil Works Contracts": {},
            "Electrical and Control Contracts": {
                "Contract Management, Contract Duration Reduction and Recording Lessons Learned from Contract Delays": {},
                "Manufacturer and Supplier Contracts for High-Voltage Substation and Transmission Line Equipment": {},
                "Contract Management of Mako and Pars Generator Plants": {},
            },
        },
        "Financial Resources": {
            "Financial Accounting Affairs": {
                "Treasury and Guarantees": {},
                "Warehouse and Assets": {},
                "Payroll": {},
                "Tax Affairs": {},
            },
            "Management Accounting": {
                "Financial Statement Preparation": {},
                "Participation in Budget Preparation": {},
                "Financing": {},
            },
            "Contract Accounting Affairs": {
                "Contract Accounting": {},
            },
            "Resource Supply Accounting": {
                "Contract Insurance": {},
                "Employer Accounting": {},
            },
        },
        "Project Handover": {
            "Provisional Handover": {},
            "Construction Defect Management and Warranty Period": {},
            "Final Handover": {},
            "Operator Personnel Training": {},
        },
        "Management System": {
            "Organizational Change Management": {
                "Changes in National Laws and Regulations": {},
                "Information Technology Changes": {},
                "Organizational Structure Changes": {},
                "Strategy Changes": {},
                "Changes to Integrated Management System Documentation": {},
            },
            "Quality Assurance (QA)": {
                "Audits": {},
                "Process Management": {},
            },
            "Knowledge Management": {},
            "Enterprise Risk Management": {
                "HSE Risk / Opportunity Management": {},
                "Process Risk / Opportunity Management": {},
                "Stakeholder Needs and Expectations Risk / Opportunity Management": {},
                "Internal and External Organizational Environment Risk / Opportunity Management": {},
            },
            "Value Engineering": {},
        },
        "Vision and Strategy Development": {
            "Strategic Analysis and Monitoring of the Internal and External Environment": {},
            "Management of Technologies Related to Current and New Products, Services and Businesses": {},
            "Formulation, Development and Monitoring of Business Objectives and Strategies": {},
            "Strategic Planning for Products and Services (Product and Service Roadmap Management)": {},
        },
        "Project Management": {
            "Resource Management": {},
            "Project Risk Management": {},
            "Project Change and Claims Management": {
                "Changes to Technical and Engineering Documents and Work Instructions": {},
                "Changes to Contractor / Supplier Contracts": {},
                "Employer Contractual Claims": {},
                "Employer Contract Changes": {},
            },
            "Cost and Budget Management": {},
            "Schedule Management": {
                "Schedule Development": {},
                "Project Control and Scheduling": {},
            },
            "Project Integration Management": {},
            "Project Quality Management": {
                "Document Control (ITP/QCP and Inspection and Test Procedures)": {},
                "Installation Quality Control": {},
                "Calibration Control": {},
                "Procurement Quality Control (Ready-Made Equipment)": {},
                "Manufacturing Quality Control": {},
                "Manufacturing Quality Control (Equipment Manufacturing Process at Factory)": {},
            },
            "Stakeholder Management": {},
            "Scope Management": {},
            "Project Management": {},
        },
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# توابع navigation
# ══════════════════════════════════════════════════════════════════════════════


def _get_subtree(tree: dict, path: list[str]) -> dict:
    """
    زیردرخت را در مسیر داده‌شده برمیگرداند.
    اگر مسیر نامعتبر باشد یا به برگ برسد → {} برمیگردد.
    """
    node = tree
    for name in path:
        if not isinstance(node, dict) or name not in node:
            return {}
        node = node[name]
    return node if isinstance(node, dict) else {}


def get_children(path: list[str]) -> list[str]:
    """
    نام فرزندان مستقیم در سطح فعلی را برمیگرداند.
    path = [] → نودهای ریشه.
    path = ["MAPNA Development"] → فرزندان آن.
    """
    sub = _get_subtree(KNOWLEDGE_TREE, path)
    return list(sub.keys())


def is_leaf(path: list[str]) -> bool:
    """آیا مسیر داده‌شده در سطح برگ است؟"""
    sub = _get_subtree(KNOWLEDGE_TREE, path)
    return sub == {}


def get_leaf_paths() -> list[list[str]]:
    """همهٔ مسیرهای کامل از ریشه تا برگ (برای ارسال به AI)."""
    paths: list[list[str]] = []

    def _walk(node: dict, prefix: list[str]):
        for name, sub in node.items():
            current = prefix + [name]
            if sub == {}:
                paths.append(current)
            else:
                _walk(sub, current)

    _walk(KNOWLEDGE_TREE, [])
    return paths


def find_path_by_leaf_name(leaf_name: str) -> list[str] | None:
    """
    اولین مسیر کامل که برگ آن برابر leaf_name باشد.
    در این درخت نام برگ‌ها یکتا است (طبق سند skill).
    """
    for path in get_leaf_paths():
        if path[-1] == leaf_name:
            return path
    return None


def render_path(path: list[str]) -> str:
    """مسیر را به شکل 'سطح۱ > سطح۲ > سطح۳ > سطح۴' برمیگرداند."""
    return " > ".join(path)


def all_paths_as_lines() -> list[str]:
    """همهٔ مسیرهای کامل به شکت لیستی از رشته‌های 'A > B > C'."""
    return [render_path(p) for p in get_leaf_paths()]


def tree_as_yaml() -> str:
    """
    درخت را به فرمت YAML-مانند (با تورفتگی) برمیگرداند.
    برای تزریق در پرامپت AI استفاده میشود.
    """
    lines: list[str] = []

    def _walk(node: dict, depth: int):
        for name, sub in node.items():
            lines.append("  " * depth + "- " + name)
            if sub:
                _walk(sub, depth + 1)

    _walk(KNOWLEDGE_TREE, 0)
    return "\n".join(lines)


def validate_path(path: list[str]) -> bool:
    """آیا مسیر داده‌شده در درخت وجود دارد؟"""
    node = KNOWLEDGE_TREE
    for name in path:
        if not isinstance(node, dict) or name not in node:
            return False
        node = node[name]
    return True


def total_leaf_count() -> int:
    """تعداد کل نودهای برگ."""
    return len(get_leaf_paths())


def total_node_count() -> int:
    """تعداد کل نودها (شامل ریشه و میانی)."""
    count = 0

    def _walk(node: dict):
        nonlocal count
        for name, sub in node.items():
            count += 1
            if sub:
                _walk(sub)

    _walk(KNOWLEDGE_TREE)
    return count