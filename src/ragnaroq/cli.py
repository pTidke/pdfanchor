"""Command line: `ragnaroq convert file.pdf --out dir`, and `reference make` / `evaluate` to measure it.

Exit codes: 0 everything accepted by rule; 2 files were written but something needs a
person; 1 the file (for `index`, any file) could not be processed, or, for `evaluate`, a file
with an answer key did not score ok; 3 `evaluate` let more critical errors through than allowed.
"""

import argparse
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, evaluate, folder, reference
from .config import Config
from .model import Document
from .ocr import EngineUnavailable, available
from .pipeline import ConversionError, convert, write
from .policy import needs_review
from .reference import PROFILES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ragnaroq", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("convert", help="Convert a PDF into document.json, document.md and chunks.jsonl")
    run.add_argument("pdf")
    run.add_argument("--out", required=True)
    run.add_argument("--ocr", choices=["auto", "none"], default="auto")
    run.add_argument("--lang", default="eng", help="OCR languages joined by +, for example eng+hin")
    run.add_argument("--dpi", type=int, default=200)
    run.add_argument("--exclude-script", action="append", default=[], help="For example Deva")
    run.add_argument(
        "--no-isolation",
        action="store_true",
        help="Read pages in this process: quicker to start, with no time or memory limit",
    )
    idx = sub.add_parser(
        "index", help="Make a folder ready for retrieval: .ragnaroq/ holds chunks.jsonl, a manifest, changes"
    )
    idx.add_argument("folder")
    idx.add_argument("--fresh", action="store_true", help="Read every file again")
    idx.add_argument("--ocr", choices=["auto", "none"], default="auto")
    idx.add_argument("--lang", default="eng", help="OCR languages joined by +, for example eng+hin")
    idx.add_argument("--dpi", type=int, default=200)
    idx.add_argument("--exclude-script", action="append", default=[], help="For example Deva")
    idx.add_argument("--file-timeout", type=float, help="Seconds a file may take to read (default 120)")
    idx.add_argument(
        "--max-cells",
        type=int,
        help="Most cells or lines read from one file that is not a PDF; the rest is cut and flagged "
        "(default 200000)",
    )
    sub.add_parser("schema", help="Print the JSON schema of document.json")
    listing = sub.add_parser("engines", help="List the OCR engines that can run here")
    ref = sub.add_parser("reference", help="Make reference PDFs whose correct text is known")
    ref_sub = ref.add_subparsers(dest="action", required=True)
    make = ref_sub.add_parser(
        "make", help="Turn born-digital PDFs into scan-like PDFs, each with a truth file of every word"
    )
    make.add_argument("pdf", nargs="+")
    make.add_argument("--out", required=True)
    make.add_argument(
        "--profile",
        action="append",
        choices=sorted(PROFILES),
        help="How to roughen the pages; repeat for several (default: all)",
    )
    make.add_argument("--pages", help='Pages of each PDF to use, for example "1-3,7" (default: all)')
    check = sub.add_parser("evaluate", help="Run the engine on references and measure how often it is wrong")
    check.add_argument("folder", help="Folder of references: *.pdf with *.truth.json beside each")
    check.add_argument("--out", required=True, help="Where evaluation.md and evaluation.json go")
    check.add_argument("--lang", default="eng")
    check.add_argument("--dpi", type=int, default=200)
    check.add_argument("--exclude-script", action="append", default=[])
    check.add_argument(
        "--min-similarity", type=float, help="Agreement threshold to evaluate (default: the policy's)"
    )
    check.add_argument("--same-letters", action="store_true", help="Evaluate the strict agreement rule")
    check.add_argument(
        "--fresh", action="store_true", help="Read every reference again instead of reusing <out>/cache"
    )
    check.add_argument(
        "--max-critical-false-accepts",
        type=float,
        help="Exit 3 when the share of accepted lines wrong on a critical token is above this, e.g. 0.001",
    )
    for command in (run, check, idx):
        command.add_argument(
            "--third-reader",
            action="store_true",
            help="Let a vision-language model settle lines the two readers disagree on",
        )
        command.add_argument("--vlm-models", help="Folder with the third reader's model files")
        command.add_argument(
            "--vlm-download",
            action="store_true",
            help="Fetch missing model files into --vlm-models, or the Hugging Face cache when that is "
            "not given",
        )
        command.add_argument(
            "--third-reader-budget", type=float, help="Seconds the third reader may spend on each document"
        )
    for command in (run, idx):
        command.add_argument("--chunk-chars", type=int, default=1200, help="Longest chunk (default 1200)")
        command.add_argument(
            "--trust-native",
            action="store_true",
            help="Accept clean text-layer lines with no OCR cross-check",
        )
        command.add_argument(
            "--same-letters",
            action="store_true",
            help="Strict: two readings must have the same letters and digits, not only be similar",
        )
        command.add_argument(
            "--page-timeout", type=float, help="Seconds a PDF page may take to read (default 60)"
        )
        command.add_argument(
            "--page-memory-mb",
            type=int,
            help="Memory the page reader (for index, also the file reader) may use (default 2048)",
        )
        command.add_argument(
            "--describe-images",
            action="store_true",
            help="Let a small model write one sentence for each image, kept as a label (model)",
        )
        command.add_argument("--describe-models", help="Folder with the image describer's model files")
        command.add_argument(
            "--describe-download",
            action="store_true",
            help="Fetch missing model files into --describe-models, or the Hugging Face cache when "
            "that is not given",
        )
    for command in (run, listing, check, idx):
        command.add_argument("--easyocr-models", help="Folder with EasyOCR's model files (the second reader)")
        command.add_argument(
            "--easyocr-download",
            action="store_true",
            help="Fetch missing EasyOCR model files into --easyocr-models",
        )
    args = parser.parse_args(argv)

    def engines_here(languages=("eng",)):
        return available(
            languages, easyocr_models=args.easyocr_models, easyocr_download=args.easyocr_download
        )

    if args.command == "schema":
        print(json.dumps(Document.model_json_schema(), indent=2))
        return 0
    if args.command == "engines":
        for engine in engines_here():
            print(engine.name, engine.version())
        return 0
    if args.command == "reference":
        made = []
        for pdf in args.pdf:
            for profile in args.profile or sorted(PROFILES):
                try:
                    chosen = reference.page_numbers(args.pages) if args.pages else None
                    pdf_path, truth_path = reference.make(pdf, profile, args.out, pages=chosen)
                except (ValueError, OSError) as error:
                    print(json.dumps({"error": str(error), "pdf": pdf, "profile": profile}), file=sys.stderr)
                    return 1
                made.append({"pdf": str(pdf_path), "truth": str(truth_path)})
        print(json.dumps({"references": made}))
        return 0
    third_reader = None
    if args.command in ("convert", "evaluate", "index") and args.third_reader:
        from .ocr.paddle_vl import PaddleOcrVl

        third_reader = PaddleOcrVl(model_dir=args.vlm_models, download=args.vlm_download)
        try:
            third_reader.prepare()
        except (EngineUnavailable, OSError) as error:
            print(json.dumps({"error": str(error)}), file=sys.stderr)
            return 1

    describer = None
    if args.command == "index" and args.describe_images:
        from .ocr.describe import SmolVlm

        describer = SmolVlm(model_dir=args.describe_models, download=args.describe_download)
        try:
            describer.check()
        except (EngineUnavailable, OSError) as error:
            print(json.dumps({"error": str(error)}), file=sys.stderr)
            return 1
    elif args.command == "convert" and args.describe_images:
        # A PDF has no figures in this version, so there is nothing to describe, and no model to load.
        print("--describe-images has no effect on a PDF: its pages have no figures yet.", file=sys.stderr)

    def with_budget(config):
        if args.third_reader_budget is None:
            return config
        settings = replace(config.third_reader, budget_seconds=args.third_reader_budget)
        return replace(config, third_reader=settings)

    if args.command == "evaluate":
        config = Config(
            dpi=args.dpi, languages=tuple(args.lang.split("+")), exclude_scripts=tuple(args.exclude_script)
        )
        if args.min_similarity is not None:
            config = replace(config, policy=replace(config.policy, min_similarity=args.min_similarity))
        if args.same_letters:
            config = replace(config, policy=replace(config.policy, same_letters=True))
        config = with_budget(config)
        try:
            report = evaluate.run(
                args.folder,
                config=config,
                engines=engines_here(config.languages),
                progress=lambda done, total, name: print(
                    f"[{done}/{total}] {name}", file=sys.stderr, flush=True
                ),
                cache=None if args.fresh else Path(args.out) / "cache",
                third_reader=third_reader,
            )
        except (ConversionError, EngineUnavailable, OSError, ValueError) as error:
            print(json.dumps({"error": str(error)}), file=sys.stderr)
            return 1
        files = evaluate.write(report, args.out)
        rates = report.total.rates()
        printed = {"total": rates, "files": {k: str(v) for k, v in files.items()}}
        failed = [f.name for f in report.exact.files if not f.ok] if report.exact else []
        if report.exact:
            printed["exact"] = {
                "files": len(report.exact.files),
                "ok": len(report.exact.files) - len(failed),
                "failed": failed,
            }
        print(json.dumps(printed))
        limit = args.max_critical_false_accepts
        too_high = limit is not None and (rates["critical_false_accept_rate"] or 0) > limit
        return 3 if too_high else 1 if failed else 0
    if args.command == "index":
        return _index(args, with_budget, engines_here, third_reader, describer)
    config = _output_options(
        args,
        Config(
            dpi=args.dpi,
            languages=tuple(args.lang.split("+")),
            exclude_scripts=tuple(args.exclude_script),
        ),
    )
    if args.no_isolation:
        config = replace(config, isolate_pages=False)
    config = with_budget(config)
    try:
        engines = engines_here(config.languages) if args.ocr == "auto" else []
        document = convert(args.pdf, config=config, engines=engines, third_reader=third_reader)
    except (ConversionError, EngineUnavailable, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    files = write(document, args.out, config)
    print(json.dumps({"summary": document.summary, "files": {k: str(v) for k, v in files.items()}}))
    return 2 if needs_review(document) else 0


def _output_options(args, config: Config) -> Config:
    """The options convert and index share: chunk size, the two policy switches, and the page limits."""
    config = replace(config, chunk_chars=args.chunk_chars)
    if args.trust_native:
        config = replace(config, policy=replace(config.policy, accept_native_without_crosscheck=True))
    if args.same_letters:
        config = replace(config, policy=replace(config.policy, same_letters=True))
    if args.page_timeout is not None:
        config = replace(config, page_timeout_seconds=args.page_timeout)
    if args.page_memory_mb is not None:
        config = replace(config, page_memory_mb=args.page_memory_mb)
    return config


def _plain(text: str) -> str:
    """A name that is safe to print: control characters (C0, C1 and DEL) written as \\xNN."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", lambda found: f"\\x{ord(found.group()):02x}", text)


def _index(args, with_budget, engines_here, third_reader, describer=None) -> int:
    config = Config(
        dpi=args.dpi, languages=tuple(args.lang.split("+")), exclude_scripts=tuple(args.exclude_script)
    )
    config = _output_options(args, config)
    if args.file_timeout is not None:
        config = replace(config, file_timeout_seconds=args.file_timeout)
    if args.max_cells is not None:
        config = replace(config, max_cells=args.max_cells)
    config = with_budget(config)
    try:
        engines = engines_here(config.languages) if args.ocr == "auto" else []
        report = folder.index(
            args.folder,
            config,
            engines=engines,
            third_reader=third_reader,
            describer=describer,
            fresh=args.fresh,
            progress=lambda path, status: print(f"{status:8} {_plain(path)}", file=sys.stderr, flush=True),
        )
    except (folder.FolderError, EngineUnavailable, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    # The same hint for many files is said once. Printing is the last thing and best-effort: a
    # closed pipe changes no exit code.
    hints = {e.error.rsplit(": ", 1)[-1] for e in report.manifest.files if e.rule == "reader.unavailable"}
    try:
        for note in report.notes:
            print(_plain(note), file=sys.stderr)
        for hint in sorted(hints):
            print(f"Some files were not read, because a library is missing: {_plain(hint)}", file=sys.stderr)
        _summary(report, args.folder)
    except OSError:
        pass
    return 1 if report.failed else 2 if report.needs_review else 0


def _summary(report, root) -> None:
    changes = report.changes
    print(
        json.dumps(
            {
                "files": {
                    status: sum(e.status == status for e in report.manifest.files)
                    for status in ("ok", "review", "failed", "skipped")
                },
                "changes": {
                    "added": len(changes.added),
                    "changed": len(changes.changed),
                    "removed": len(changes.removed),
                    "chunks_added": len(changes.chunks_added),
                    "chunks_changed": len(changes.chunks_changed),
                    "chunks_removed": len(changes.chunks_removed),
                },
                "out": str(Path(root) / folder.STATE),
            }
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
