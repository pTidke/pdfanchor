"""Command line: `ragnaroq convert file.pdf --out dir`, and `reference make` / `evaluate` to measure it.

Exit codes: 0 everything accepted by rule; 2 files were written but something needs a
person; 1 the file could not be processed.
"""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, evaluate, reference
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
    run.add_argument("--chunk-chars", type=int, default=1200)
    run.add_argument("--trust-native", action="store_true", help="Accept clean text-layer lines with no OCR cross-check")
    run.add_argument("--page-timeout", type=float, help="Seconds a page may take to read (default 60)")
    run.add_argument("--page-memory-mb", type=int, help="Memory the page reader may use (default 2048)")
    run.add_argument(
        "--no-isolation",
        action="store_true",
        help="Read pages in this process: quicker to start, with no time or memory limit",
    )
    run.add_argument(
        "--same-letters",
        action="store_true",
        help="Strict: two readings must have the same letters and digits, not only be similar",
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
    check.add_argument("--min-similarity", type=float, help="Agreement threshold to evaluate (default: the policy's)")
    check.add_argument("--same-letters", action="store_true", help="Evaluate the strict agreement rule")
    check.add_argument(
        "--fresh", action="store_true", help="Read every reference again instead of reusing <out>/cache"
    )
    check.add_argument(
        "--max-critical-false-accepts",
        type=float,
        help="Exit 3 when the share of accepted lines wrong on a critical token is above this, e.g. 0.001",
    )
    for command in (run, check):
        command.add_argument(
            "--third-reader",
            action="store_true",
            help="Let a vision-language model settle lines the two readers disagree on",
        )
        command.add_argument("--vlm-models", help="Folder with the third reader's model files")
        command.add_argument(
            "--vlm-download", action="store_true", help="Fetch missing model files into --vlm-models"
        )
        command.add_argument(
            "--third-reader-budget", type=float, help="Seconds the third reader may spend in all"
        )
    for command in (run, listing, check):
        command.add_argument(
            "--easyocr-models", help="Folder with EasyOCR's model files (the second reader)"
        )
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
    if args.command in ("convert", "evaluate") and args.third_reader:
        from .ocr.paddle_vl import PaddleOcrVl

        third_reader = PaddleOcrVl(model_dir=args.vlm_models, download=args.vlm_download)
        try:
            third_reader.prepare()
        except EngineUnavailable as error:
            print(json.dumps({"error": str(error)}), file=sys.stderr)
            return 1

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
                progress=lambda done, total, name: print(f"[{done}/{total}] {name}", file=sys.stderr, flush=True),
                cache=None if args.fresh else Path(args.out) / "cache",
                third_reader=third_reader,
            )
        except (ConversionError, EngineUnavailable, OSError, ValueError) as error:
            print(json.dumps({"error": str(error)}), file=sys.stderr)
            return 1
        files = evaluate.write(report, args.out)
        rates = report.total.rates()
        print(json.dumps({"total": rates, "files": {k: str(v) for k, v in files.items()}}))
        limit = args.max_critical_false_accepts
        too_high = limit is not None and (rates["critical_false_accept_rate"] or 0) > limit
        return 3 if too_high else 0
    config = Config(
        dpi=args.dpi,
        languages=tuple(args.lang.split("+")),
        exclude_scripts=tuple(args.exclude_script),
        chunk_chars=args.chunk_chars,
    )
    if args.trust_native:
        config = replace(config, policy=replace(config.policy, accept_native_without_crosscheck=True))
    if args.same_letters:
        config = replace(config, policy=replace(config.policy, same_letters=True))
    if args.page_timeout is not None:
        config = replace(config, page_timeout_seconds=args.page_timeout)
    if args.page_memory_mb is not None:
        config = replace(config, page_memory_mb=args.page_memory_mb)
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


if __name__ == "__main__":
    raise SystemExit(main())
