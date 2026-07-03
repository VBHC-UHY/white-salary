"""编程工具 — 15个代码相关工具。"""
import re
from ._helpers import tool, P, S


@tool("write_code", "根据需求写代码", P(language=S("编程语言"), description=S("需求描述", True)))
async def write_code(language: str = "python", description: str = "") -> str:
    return f"[写代码] 语言: {language}\n需求: {description}\n请用代码块格式回答。"

@tool("explain_code", "解释代码功能", P(code=S("代码", True)))
async def explain_code(code: str = "") -> str:
    return f"[解释代码]\n```\n{code[:500]}\n```\n请逐行解释。"

@tool("fix_code", "修复代码bug", P(code=S("代码", True), error=S("错误信息")))
async def fix_code(code: str = "", error: str = "") -> str:
    return f"[修复代码]\n```\n{code[:500]}\n```\n错误: {error}\n请找出bug并修复。"

@tool("optimize_code", "优化代码性能和可读性", P(code=S("代码", True)))
async def optimize_code(code: str = "") -> str:
    return f"[优化代码]\n```\n{code[:500]}\n```\n请优化性能和可读性。"

@tool("review_code", "代码审查", P(code=S("代码", True)))
async def review_code(code: str = "") -> str:
    return f"[代码审查]\n```\n{code[:500]}\n```\n请指出问题和改进建议。"

@tool("convert_code", "转换代码语言", P(code=S("代码", True), target_lang=S("目标语言", True)))
async def convert_code(code: str = "", target_lang: str = "python") -> str:
    return f"[转换代码] → {target_lang}\n```\n{code[:500]}\n```"

@tool("generate_tests", "生成单元测试", P(code=S("代码", True)))
async def generate_tests(code: str = "") -> str:
    return f"[生成测试]\n```\n{code[:500]}\n```\n请编写单元测试。"

@tool("generate_docs", "生成代码文档", P(code=S("代码", True)))
async def generate_docs(code: str = "") -> str:
    return f"[生成文档]\n```\n{code[:500]}\n```\n请生成完整文档注释。"

@tool("design_algorithm", "设计算法并分析复杂度", P(problem=S("问题", True)))
async def design_algorithm(problem: str = "") -> str:
    return f"[算法设计] {problem}\n请设计算法并给出伪代码和复杂度分析。"

@tool("design_architecture", "设计系统架构", P(requirements=S("需求", True)))
async def design_architecture(requirements: str = "") -> str:
    return f"[架构设计] {requirements}\n请设计系统架构。"

@tool("design_api", "设计RESTful API", P(description=S("描述", True)))
async def design_api(description: str = "") -> str:
    return f"[API设计] {description}\n请设计REST API。"

@tool("debug", "调试代码bug", P(code=S("代码", True), symptom=S("故障症状")))
async def debug_code(code: str = "", symptom: str = "") -> str:
    return f"[调试] 症状: {symptom}\n```\n{code[:500]}\n```\n请分析原因并提供调试步骤。"

@tool("regex", "正则表达式测试", P(pattern=S("正则", True), test_text=S("测试文本")))
async def regex_helper(pattern: str = "", test_text: str = "") -> str:
    if not pattern:
        return "请提供正则表达式"
    try:
        matches = re.findall(pattern, test_text or "")
        return f"正则: {pattern}\n匹配: {matches}" if matches else f"正则: {pattern}\n无匹配"
    except re.error as e:
        return f"正则语法错误: {e}"

@tool("sql", "SQL语句检查和优化", P(query=S("SQL语句", True)))
async def sql_helper(query: str = "") -> str:
    return f"[SQL]\n```sql\n{query}\n```\n请检查并优化。"

@tool("refactor", "重构代码", P(code=S("代码", True), goal=S("重构目标")))
async def refactor_code(code: str = "", goal: str = "") -> str:
    return f"[重构] 目标: {goal}\n```\n{code[:500]}\n```"

@tool("coding_helper", "编程问题辅助", P(question=S("编程问题", True)))
async def coding_helper(question: str = "") -> str:
    return f"[编程问题] {question}\n请用代码块格式回答。"

@tool("execute_code",
      "执行Python代码。也能控制用户电脑：打开应用、点击、输入文字、截屏、打开网页等。"
      "PC控制时可直接调用预置函数：click(x,y), type_text('文本'), open_app('notepad'), "
      "open_url('baidu.com'), hotkey('ctrl','c'), press('enter'), smart_click('确定按钮'), "
      "scroll(3), wait(2), run_command('dir'), get_window_list(), screenshot() 等。"
      "当用户说'帮我打开xx'、'帮我点xx'、'帮我输入xx'时使用此工具。",
      P(code=S("Python代码（可直接使用PC控制函数，无需import）", True)))
async def execute_code(code: str = "") -> str:
    from white_salary.adapters.tools.code_executor import execute_python
    return await execute_python(code)


# 2026-07-02 审计修复（批2）：下架15个提示词复读空壳（write_code/explain_code/fix_code/
# optimize_code/review_code/convert_code/generate_tests/generate_docs/design_algorithm/
# design_architecture/design_api/debug/sql/refactor/coding_helper）——它们只把入参包一层
# 「[写代码]…请回答」文本返回，无任何实际处理，主模型本来就能直接回答，
# 还显著加重了149工具payload导致的判断超时（依据 docs/audit-2026-07-02/tools-media.json）。
# 只保留真正干活的 regex 和 execute_code；函数体保留，便于以后接真实现。
TOOLS = [fn._tool_def for fn in [
    regex_helper, execute_code,
]]
