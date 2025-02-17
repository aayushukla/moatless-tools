import fnmatch
import logging

import instructor
from pydantic import BaseModel, ConfigDict, Field

from moatless.file_context import FileContext, RankedFileSpan
from moatless.state import ActionResponse, AgenticState
from moatless.types import (
    ActionRequest,
    AssistantMessage,
    Message,
    UserMessage,
)

logger = logging.getLogger(__name__)


SEARCH_SYSTEM_PROMPT = """You are an autonomous AI assistant.
Your task is to locate the code relevant to an issue.

# Instructions:

1. Understand The Issue:
Read the <issue> tag to understand the issue.

2. Review Current File Context:
Examine the <file_context> tag to see which files and code spans have already been identified.
If you believe that all relevant files have been identified, you can finish the search by setting complete to true.

3. Consider the Necessary Search Parameters:
Determine if specific file types, directories, function or class names or code patterns are mentioned in the issue.
If you can you should always try to specify the search parameters as accurately as possible.

4. Ensure At Least One Search Parameter:
Make sure that at least one of query, code_snippet, class_name, or function_name is provided in each search request.

5. Formulate the Search function:
Set at least one of the search paramaters `query`, `code_snippet`, `class_name` or `function_name`.
"""


SEARCH_FUNCTIONS_FEW_SHOT_OPENAI_FUNC = """
6. Execute the Search function:
Use the Search function with the search parameters and your thoughts on how to approach this task.

Think step by step and write out your thoughts in the thoughts field.

Examples:

User:
The file uploader intermittently fails with "TypeError: cannot unpack non-iterable NoneType object". This issue appears sporadically during high load conditions..

AI Assistant:
functions.Search({
    query: "File upload process to fix intermittent 'TypeError: cannot unpack non-iterable NoneType object'",
    file_pattern: "**/uploader/**/*.py"
)

User:
There's a bug in the PaymentProcessor class where transactions sometimes fail to log correctly, resulting in missing transaction records.

AI Assistant:
functions.Search({
    class_name: "PaymentProcessor"
)

User:
The generate_report function sometimes produces incomplete reports under certain conditions. This function is part of the reporting module. Locate the generate_report function in the reports directory to debug and fix the issue.

AI Assistant:
functions.Search({
    function_name: "generate_report",
    file_pattern: "**/reports/**/*.py"
)

User:
The extract_data function in HTMLParser throws an "AttributeError: 'NoneType' object has no attribute 'find'" error when parsing certain HTML pages.

AI Assistant:
functions.Search({
    class_name: "HTMLParser",
    function_name: "extract_data"
)

User:
The database connection setup is missing SSL configuration, causing insecure connections.

Here’s the stack trace of the error:

File "/opt/app/db_config/database.py", line 45, in setup_connection
    engine = create_engine(DATABASE_URL)
File "/opt/app/db_config/database.py", line 50, in <module>
    connection = setup_connection()

AI Assistant:
functions.Search({
    code_snippet: "engine = create_engine(DATABASE_URL)",
    file_pattern: "db_config/database.py"
)
"""

SEARCH_FUNCTIONS_FEW_SHOT = """6. Execute the Search function:
Use the Search function with the search parameters and your thoughts on how to approach this task.

Think step by step and write out your thoughts in the scratch_pad field.

Examples:

User:
The file uploader intermittently fails with "TypeError: cannot unpack non-iterable NoneType object". This issue appears sporadically during high load conditions..

Search parameters:
    query: "File upload process to fix intermittent 'TypeError: cannot unpack non-iterable NoneType object'",
    file_pattern: "**/uploader/**/*.py"


User:
There's a bug in the PaymentProcessor class where transactions sometimes fail to log correctly, resulting in missing transaction records.

Search parameters:
    class_name: "PaymentProcessor"


User:
The generate_report function sometimes produces incomplete reports under certain conditions. This function is part of the reporting module. Locate the generate_report function in the reports directory to debug and fix the issue.

Search parameters:
    function_name: "generate_report",
    file_pattern: "**/reports/**/*.py"


User:
The extract_data function in HTMLParser throws an "AttributeError: 'NoneType' object has no attribute 'find'" error when parsing certain HTML pages.

Search parameters:
    class_name: "HTMLParser",
    function_name: "extract_data"


User:
The database connection setup is missing SSL configuration, causing insecure connections.

Here’s the stack trace of the error:

File "/opt/app/db_config/database.py", line 45, in setup_connection
    engine = create_engine(DATABASE_URL)
File "/opt/app/db_config/database.py", line 50, in <module>
    connection = setup_connection()

Search parameters:
    code_snippet: "engine = create_engine(DATABASE_URL)",
    file_pattern: "db_config/database.py"

"""

SEARCH_JSON_FEW_SHOT = """6. Execute the Search:
Execute the search by providing the search parameters and your thoughts on how to approach this task in a JSON object. 

Think step by step and write out your thoughts in the scratch_pad field.

Examples:

User:
The file uploader intermittently fails with "TypeError: cannot unpack non-iterable NoneType object". This issue appears sporadically during high load conditions..

Assistant:
{
 "scratch_pad": "The error indicates that a variable expected to be iterable is None, which might be happening due to race conditions or missing checks under high load. Investigate the file upload logic to ensure all necessary checks are in place and improve concurrency handling.",
 "file_pattern": "**/uploader/**/*.py",
 "query": "TypeError: cannot unpack non-iterable NoneType object"
}

User:
There's a bug in the PaymentProcessor class where transactions sometimes fail to log correctly, resulting in missing transaction records.

Assistant:
{
  "scratch_pad": "Missing transaction logs can cause significant issues in tracking payments. The problem may be related to how the logging mechanism handles transaction states or errors. Investigate the PaymentProcessor class, focusing on the transaction logging part.",
  "class_name": "PaymentProcessor",
  "query": "transactions fail to log correctly"
}

User:
The generate_report function sometimes produces incomplete reports under certain conditions. This function is part of the reporting module. Locate the generate_report function in the reports directory to debug and fix the issue.

Assistant:
{
  "scratch_pad": "Incomplete reports suggest that the function might be encountering edge cases or unhandled exceptions that disrupt the report generation. Reviewing the function's logic and error handling in the reporting module is necessary.",
  "function_name": "generate_report",
  "file_pattern": "**/reports/**/*.py",
}

User:
The extract_data function in HTMLParser throws an "AttributeError: 'NoneType' object has no attribute 'find'" error when parsing certain HTML pages.

Assistant:
{
  "scratch_pad": "The error occurs when 'find' is called on a NoneType object, suggesting that the HTML structure might not match expected patterns. ",
  "class_name": "HTMLParser",
  "function_name": "extract_data",
}


User:
The database connection setup is missing SSL configuration, causing insecure connections.

Here’s the stack trace of the error:

File "/opt/app/db_config/database.py", line 45, in setup_connection
    engine = create_engine(DATABASE_URL)
File "/opt/app/db_config/database.py", line 50, in <module>
    connection = setup_connection()

Assistant:
{
  "scratch_pad": "The missing SSL configuration poses a security risk by allowing unencrypted connections. Find the code snippet `engine = create_engine(DATABASE_URL)` provided in the issue.",
  "code_snippet": "engine = create_engine(DATABASE_URL)",
}
"""

IGNORE_TEST_PROMPT = (
    "Test files are not in the search scope. Ignore requests to search for tests. "
)


class Search(ActionRequest):
    """Take action to search for code, identify found and finish up."""

    scratch_pad: str = Field(
        description="Your thoughts on what search parameters to set."
    )

    file_pattern: str | None = Field(
        default=None,
        description="A glob pattern to filter search results to specific file types or directories. ",
    )

    query: str | None = Field(
        default=None,
        description="A semantic similarity search query. Use natural language to describe what you are looking for.",
    )

    code_snippet: str | None = Field(
        default=None,
        description="Specific code snippet to that should be exactly matched.",
    )

    class_names: list[str] = Field(
        default=[], description="Specific class names to include in the search."
    )

    function_names: list[str] = Field(
        default=[], description="Specific function names to include in the search."
    )

    complete: bool | None = Field(
        default=False, description="Set to true when the search is complete."
    )

    def has_search_attributes(self):
        return any(
            [
                self.query,
                self.code_snippet,
                self.class_names,
                self.function_names,
            ]
        )


class ActionCallWithContext(BaseModel):
    action: ActionRequest
    file_context: FileContext
    message: str | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SearchCode(AgenticState):
    message: str | None = Field(
        None,
        description="Message to the search",
    )

    max_search_results: int = Field(
        25,
        description="The maximum number of search results.",
    )

    max_retries_with_any_file_context: int = Field(
        3,
        description="The maximum number of retries when there are identified files in file context.",
    )

    provide_initial_context: bool = True
    initial_context_tokens: int = 4000
    initial_search_results: int = 50
    initial_context_spans_per_file: int = 5

    support_test_files: bool = False

    def __init__(
        self,
        message: str | None = None,
        max_search_results: int = 25,
        max_retries_with_any_file_context: int = 3,
        provide_initial_context: bool = True,
        initial_context_tokens: int = 4000,
        initial_search_results: int = 50,
        initial_context_spans_per_file: int = 5,
        **data,
    ):
        super().__init__(
            message=message,
            include_message_history=True,
            provide_initial_context=provide_initial_context,
            max_search_results=max_search_results,
            max_retries_with_any_file_context=max_retries_with_any_file_context,
            initial_context_tokens=initial_context_tokens,
            initial_search_results=initial_search_results,
            initial_context_spans_per_file=initial_context_spans_per_file,
            **data,
        )

    def handle_action(self, action: Search) -> ActionResponse:
        if action.complete:
            return ActionResponse.transition(
                "finish",
                output={
                    "message": action.scratch_pad,
                },
            )

        if not action.has_search_attributes():
            return self._retry(
                "You must provide at least one the search attributes query, code_snippet, class_name or function_name to search. If you're finished, set finished to true."
            )

        dup_error = self._duplicate_search(action)
        if dup_error:
            message = dup_error

            if action.file_pattern:
                message += f"\n* **File Pattern:** `{action.file_pattern}`"
            if action.query:
                message += f"\n* **Query:** `{action.query}`"
            if action.code_snippet:
                message += f"\n* **Code Snippet:** `{action.code_snippet}`"
            if action.class_names:
                message += f"\n* **Class Name:** `{action.class_names}`"
            if action.function_names:
                message += f"\n* **Function Name:** `{action.function_names}`"

            message += "\n\nPlease provide a new search parameters."
            return self._retry(message)

        if (
            not self.support_test_files
            and action.file_pattern
            and is_test_pattern(action.file_pattern)
        ):
            return self._retry("It's not possible to search for test files.")

        search_result = self.workspace.code_index.search(
            file_pattern=action.file_pattern,
            query=action.query,
            code_snippet=action.code_snippet,
            class_names=action.class_names,
            function_names=action.function_names,
            max_results=self.max_search_results,
        )

        logger.info(f"Found {len(search_result.hits)} hits.")

        ranked_spans = []
        for hit in search_result.hits:
            for span in hit.spans:
                ranked_spans.append(
                    RankedFileSpan(
                        file_path=hit.file_path,
                        span_id=span.span_id,
                        rank=span.rank,
                        tokens=span.tokens,
                    )
                )

        if len(ranked_spans) == 0:
            logger.info("No search results found. Will retry.")

            message = "I searched using the following parameters:\n"

            if action.file_pattern:
                message += f"\n* **File Pattern:** `{action.file_pattern}`"
            if action.query:
                message += f"\n* **Query:** `{action.query}`"
            if action.code_snippet:
                message += f"\n* **Code Snippet:** `{action.code_snippet}`"
            if action.class_names:
                message += f"\n* **Class Names:** `{','.join(action.class_names)}`"
            if action.function_names:
                message += (
                    f"\n* **Function Names:** `{','.join(action.function_names)}`"
                )

            message += "\n\nUnfortunately, I didn’t find any relevant results."
            message += search_result.message

            return self._retry(message)

        output = {"ranked_spans": ranked_spans}
        output.update(action.dict(exclude={"scratch_pad"}))

        return ActionResponse.transition(
            trigger="did_search",
            output=output,
        )

    def _retry(self, message: str) -> ActionResponse:
        if (
            self.retries() >= self.max_retries_with_any_file_context
            and self.file_context.files
        ):
            logger.info(
                "Exceeded max retries, will finish as there are identified files in the file context. Transitioning to finish."
            )
            return ActionResponse.transition("finish")
        else:
            return ActionResponse.retry(message)

    def _duplicate_search(self, action: Search) -> str | None:
        previous_transitions = self.loop.trajectory.get_transitions(str(self))
        for transition in previous_transitions:
            for previous_action in transition.actions:
                if isinstance(previous_action.action, Search):
                    err_message = ""
                    exclude = {"scratch_pad"}
                    if action.function_names or action.class_names:
                        exclude.add("query")

                        err_message = ""
                        if (
                            action.function_names
                            == previous_action.action.function_names
                        ):
                            err_message += f"You already searched for the function name: {action.function_names}"
                        if action.class_names == previous_action.action.class_names:
                            err_message += f"You already searched for the class name: {action.class_names}"

                    previous = previous_action.action.model_dump(
                        exclude={"scratch_pad"}
                    )
                    current = action.model_dump(exclude={"scratch_pad"})
                    if previous == current:
                        return (
                            "You already did a search with the same parameters. "
                            + err_message
                        )

        return None

    def action_type(self) -> type[BaseModel] | None:
        return Search

    def system_prompt(self) -> str:
        system_prompt = SEARCH_SYSTEM_PROMPT

        if self.loop.instructor_mode == instructor.Mode.JSON:
            system_prompt += SEARCH_JSON_FEW_SHOT
        elif self.model.startswith("openai"):
            system_prompt += SEARCH_FUNCTIONS_FEW_SHOT_OPENAI_FUNC
        else:
            system_prompt += SEARCH_FUNCTIONS_FEW_SHOT

        if not self.support_test_files:
            system_prompt += IGNORE_TEST_PROMPT
        return system_prompt

    def messages(self) -> list[Message]:
        messages: list[Message] = []

        content = f"<issue>\n{self.loop.trajectory.initial_message}\n</issue>"

        if self.provide_initial_context:
            result = self.workspace.code_index.semantic_search(
                query=self.loop.trajectory.initial_message,
                exact_match_if_possible=False,
                max_spans_per_file=5,
                max_results=50,
            )

            file_context = self.create_file_context(max_tokens=4000)

            for hit in result.hits:
                for span in hit.spans:
                    file_context.add_span_to_context(
                        hit.file_path, span.span_id, tokens=1
                    )

            content += "\n\nHere's some files that might be relevant when formulating the search.\n"
            content += file_context.create_prompt(
                show_span_ids=False,
                show_line_numbers=False,
                exclude_comments=True,
                show_outcommented_code=False,
            )

        previous_transitions = self.loop.trajectory.get_transitions(str(self))
        for transition in previous_transitions:
            if transition.state.message:
                content += transition.state.message
            messages.append(UserMessage(content=content))
            messages.append(
                AssistantMessage(
                    action=transition.actions[-1].action,
                )
            )
            content = ""

        if self.message:
            content += f"\n\n{self.message}\n"

        if self.file_context.files:
            file_context_str = self.file_context.create_prompt(
                exclude_comments=True,
                show_outcommented_code=True,
                outcomment_code_comment="... rest of the code",
            )
        else:
            file_context_str = "No files found yet."

        content += f"\n\n<file_context>\n{file_context_str}\n</file_context>"

        messages.append(UserMessage(content=content))
        messages.extend(self.retry_messages())

        return messages


def is_test_pattern(file_pattern: str):
    test_patterns = ["test_*.py", "/tests/"]
    for pattern in test_patterns:
        if pattern in file_pattern:
            return True

    if file_pattern.startswith("test"):
        return True

    test_patterns = ["test_*.py"]

    return any(fnmatch.filter([file_pattern], pattern) for pattern in test_patterns)
