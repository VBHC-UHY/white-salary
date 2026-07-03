"""
测试代码执行器 code_executor.py 的安全逻辑。

重点测"安全拦截"——危险代码在真正运行前就被拦下返回，所以测试不会真执行危险操作。
也测一条安全代码能正常跑出结果，以及 _wrap_code 自动打印末行表达式。
"""

from white_salary.adapters.tools.code_executor import execute_python, _wrap_code


class TestCodeExecutorSafety:
    async def test_always_blocked_rmtree(self) -> None:
        """删目录这类操作即便在PC模式也禁止，运行前拦截。"""
        r = await execute_python("import shutil\nshutil.rmtree('/tmp/whatever')")
        assert "安全拦截" in r
        assert "shutil.rmtree" in r

    async def test_sandbox_blocks_dangerous_import(self) -> None:
        r = await execute_python("import socket\nprint('x')")
        assert "安全拦截" in r
        assert "socket" in r

    async def test_sandbox_blocks_eval(self) -> None:
        r = await execute_python("eval('2+2')")
        assert "安全拦截" in r

    async def test_empty_code(self) -> None:
        r = await execute_python("")
        assert "请提供" in r

    async def test_safe_code_runs(self) -> None:
        """普通安全代码应真正执行并返回输出。"""
        r = await execute_python("print('HELLO_WS_TEST')")
        assert "HELLO_WS_TEST" in r


class TestWrapCode:
    def test_wraps_last_expression(self) -> None:
        wrapped = _wrap_code("1 + 1")
        assert "__r__ = 1 + 1" in wrapped
        assert "print(__r__)" in wrapped

    def test_does_not_wrap_assignment(self) -> None:
        wrapped = _wrap_code("x = 5")
        assert "__r__" not in wrapped
