import json
import sys
import types
import unittest
from unittest.mock import patch

from recorder import summarize


class OpenAISummarizeTests(unittest.TestCase):
    def test_api_uses_openai_key_model_and_structured_output(self):
        response_data = {"title": "Planning", "summary": "Aligned on next steps."}
        message = types.SimpleNamespace(content=json.dumps(response_data), refusal=None)
        choice = types.SimpleNamespace(message=message, finish_reason="stop")
        completion = types.SimpleNamespace(choices=[choice])
        completions = types.SimpleNamespace(create=unittest.mock.Mock(return_value=completion))
        client = types.SimpleNamespace(chat=types.SimpleNamespace(
            completions=completions,
        ))
        openai = types.SimpleNamespace(OpenAI=unittest.mock.Mock(return_value=client))

        with (
            patch.dict(sys.modules, {"openai": openai}),
            patch.object(summarize.config, "get_secret", return_value="sk-test"),
        ):
            result = summarize._via_api("system", "prompt", {"openai_model": "gpt-4.1"})

        self.assertEqual(result, response_data)
        openai.OpenAI.assert_called_once_with(api_key="sk-test")
        request = completions.create.call_args.kwargs
        self.assertEqual(request["model"], "gpt-4.1")
        self.assertEqual(request["messages"], [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "prompt"},
        ])
        self.assertEqual(request["response_format"]["json_schema"]["schema"], summarize.SCHEMA)

    def test_api_requires_openai_key(self):
        openai = types.SimpleNamespace(OpenAI=unittest.mock.Mock())
        with (
            patch.dict(sys.modules, {"openai": openai}),
            patch.object(summarize.config, "get_secret", return_value=""),
            self.assertRaisesRegex(summarize.SummaryError, "Add an OpenAI API key"),
        ):
            summarize._via_api("system", "prompt", {})
        openai.OpenAI.assert_not_called()

    def test_claude_cli_environment_excludes_openai_key(self):
        with patch.dict(summarize.os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            self.assertNotIn("OPENAI_API_KEY", summarize.cli_env())


if __name__ == "__main__":
    unittest.main()
