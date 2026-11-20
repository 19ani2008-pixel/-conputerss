# Flask backend

Requires Python 3.11 or newer.

## Setup

1. Create and activate a virtual environment with Python 3.11 or newer.
2. Install dependencies with `python -m pip install -r requirements.txt`.
3. Add your MongoDB connection string to `MONGODB_URI` in `.env`.
4. Start the development server with `python app.py`.

The health endpoint is available at `http://127.0.0.1:5000/api/health`.

Leave `MONGODB_URI` blank to run code analysis without account routes.

## Languages

BugRadar works with any programming language. About 100 languages have
built-in checks that run locally and need no API key. Any other language name
is accepted too and is reviewed by the AI model.

`GET /api/languages` lists the built-in languages with their file extensions
and file names. Examples: Python, JavaScript, TypeScript, Java, C, C++, C#, Go,
Rust, Ruby, PHP, Swift, Kotlin, Dart, Scala, SQL, Bash, PowerShell, R, Lua,
Julia, Perl, Haskell, Elixir, Clojure, Erlang, OCaml, F#, Zig, Solidity,
HTML, CSS, JSON, YAML, TOML, Dockerfile, Terraform, GraphQL and more.

### How a file is analyzed

| Engine | Used for | What it does |
| --- | --- | --- |
| `python-ast` | Python | The original AST checks |
| `tree-sitter` | The other built-in languages | Parser-based syntax errors, then the rules below |
| `ai` | Everything else | The AI model finds the issues (needs `GROQ_API_KEY`) |

Syntax checks use tree-sitter grammars that ship inside the
`tree-sitter-language-pack` wheel, so no language toolchain or network access
is needed. Code is parsed, never run.

After the syntax check passes, rule packs look for bugs and risks. Comments
and string literals are blanked out first, so `eval(` inside a string or a
comment is not reported. Rules include:

- Unsafe dynamic execution (`eval`, `new Function`, `Invoke-Expression`, ...)
- Shell and process execution, unsafe C functions (`gets`, `strcpy`, ...)
- Empty and overly broad exception handlers, empty Go `if err != nil` blocks
- Division or modulo by an integer zero
- Assignment inside `if`, `<=` loop bounds, NaN and `typeof` comparisons,
  Java string `==`, loose equality in JavaScript and TypeScript
- Unreachable code after `return`, `throw`, `break`, `continue` or `goto`
- SQL `DELETE` or `UPDATE` without `WHERE`, `= NULL`
- Dangerous shell lines (`rm -rf "$VAR/"`, `curl | sh`) and unpinned Docker images
- Hardcoded passwords, API keys and private keys in any language or config file

Findings have the same shape for every language:
`{line, column, message, type, severity}` with severity `High`, `Medium` or `Low`.

### Accuracy

The grammars were tested on valid code from real projects (150 to 300 files
per language, usually from one or two projects). Any syntax error reported on
valid code is a false positive.

| Group | Languages | Syntax errors reported on valid code |
| --- | --- | --- |
| Well tested | JavaScript, TypeScript, Java, PHP, Rust, Go, Ruby, Lua, R, Elixir, Haskell, Kotlin, C#, Scala, JSON, YAML, TOML, XML | 0% to about 4% of files |
| Approximate | C, C++, Objective-C, SQL, SCSS, CSS, Swift, Dart, Bash | 8% to 93%, mostly macros, templates and dialects |
| Not measured | All other built-in languages | Unknown |

For the approximate and not measured groups, syntax findings are reported as
`Possible Syntax Error` with `Medium` severity and a note to confirm with your
compiler or interpreter. The other rules still run. Syntax errors in the well
tested group are reported as `Syntax Error` with `High` severity and stop the
scan, as they do for Python.

Rule findings were reviewed against the same projects and tightened until the
common false positives were gone, but they are heuristics. Treat them as
leads to review, not proof.

### Request fields

`POST /api/analyze` and `POST /api/ai-repair` accept:

- `code` (required): the source text.
- `language` (optional): a name or alias such as `Go`, `golang`, `c++`, `c#`,
  `js`. Use `auto` to detect it. It defaults to Python when omitted and no
  `filename` is sent.
- `filename` (optional): used for detection, for example `Main.java` or
  `Dockerfile`. Detection also reads shebang lines and recognizes the content
  of common languages. If it cannot decide, the API returns a 400 asking for a
  language.

Responses include `language` (the resolved name) and `engine`.

Built-in languages are analyzed without calling the AI. `POST /api/ai-repair`
sends the local findings to the AI model along with the code, and works for
every language when `GROQ_API_KEY` is set.

### Checking AI repairs

Suggested repairs are syntax-checked in the language they are written in.
`patch_validation.valid` is `true` or `false` when the parser can judge, and
`null` when it cannot (other languages, or the approximate group when it
reports a possible problem). The interface lets you apply an unchecked repair
after you review it.

## Project analysis

The React interface can upload individual files or a selected folder in any
supported language. Folder analysis uploads source files to
`POST /api/analyze-project`, where job progress is available from
`GET /api/jobs/<job_id>`. Each file result includes its `language` and
`engine`.

Dependency and build folders (`node_modules`, `vendor`, `target`, `dist`,
`build`, `.git` and similar), minified files, lock files, binary files and files
over 1 MB are skipped. Prose and data files such as Markdown and CSV are
skipped in folder scans.

AI-generated patches are syntax-checked and tested in Docker when Docker is
available. Docker runs use a read-only project mount, disabled networking,
memory/CPU limits, and a process limit. If Docker is unavailable, the backend
does not execute uploaded code and reports the sandbox as unavailable.

Docker test validation supports Python only. For other languages the sandbox
step is reported as skipped and patches rely on the parser-based syntax check.

## Configuration

Set these in `.env` (see `.env.example`):

- `GROQ_API_KEY`: needed for AI repairs and for languages without built-in checks.
- `GROQ_MODEL`: defaults to `openai/gpt-oss-20b`.
- `GROQ_MAX_TOKENS`: output limit for AI replies, default `4000`. Verbose
  languages need more room than the previous fixed `1500`.
- `BUGRADAR_MAX_PARSE_BYTES`: largest file analyzed with the built-in parser
  checks, default `100000` (100 KB). Larger files get a Low severity
  `Analysis Skipped` finding.
- `BUGRADAR_PARSE_TIMEOUT`: seconds a single parse may take, default `3`.

### Limits

Single-file requests are limited to 1 MB. The parser is limited further, as
tree-sitter can use a great deal of memory and time on crafted or garbage
input. During testing, 200 KB of repeated unterminated strings needed about
4 GB of memory. Real source files of 100 KB parse in milliseconds. The
pattern checks have bounded scans, so they stay fast on large inputs. If you
expose this service beyond your own machine, also run it with a memory limit.

## Tests

```sh
python -m unittest discover -s tests
```
