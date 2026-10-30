"""Frozen entry point: retain startup errors instead of closing the console."""
import argparse
import faulthandler
from pathlib import Path
import sys
import traceback


def main():
    parser = argparse.ArgumentParser(description='企业RAG系统')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--check-startup', action='store_true', help='检查打包依赖及临时向量库后退出')
    args = parser.parse_args()
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
    log_dir = base / 'data' / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / 'startup.log').open('a', encoding='utf-8') as startup_log:
        faulthandler.enable(startup_log)
        try:
            from app.main import run
            if args.check_startup:
                from app.agents.graph import rag_graph
                from tempfile import TemporaryDirectory
                import chromadb
                from chromadb.config import Settings
                from app.vectorstore.chroma_store import DummyEmbeddingFunction
                with TemporaryDirectory(ignore_cleanup_errors=True) as directory:
                    client = chromadb.PersistentClient(path=directory, settings=Settings(anonymized_telemetry=False))
                    collection = client.get_or_create_collection('startup-check', embedding_function=DummyEmbeddingFunction())
                    collection.add(ids=['sample'], embeddings=[[1., 0., 0.]], documents=['startup check'])
                    result = collection.query(query_embeddings=[[1., 0., 0.]], n_results=1)
                    if result['ids'] != [['sample']]:
                        raise RuntimeError('ChromaDB 启动自检失败')
                print('Startup check passed: API, agent graph and local vector storage', flush=True)
                return 0
            run(port=args.port, open_browser=not args.no_browser)
            return 0
        except (Exception, SystemExit) as exc:
            traceback.print_exc(file=startup_log)
            startup_log.flush()
            traceback.print_exc()
            print(f'\n启动失败，错误日志：{log_dir / "startup.log"}', flush=True)
            if not args.check_startup and sys.stdin is not None and sys.stdin.isatty():
                try:
                    input('按 Enter 关闭窗口...')
                except (EOFError, KeyboardInterrupt):
                    pass
            return exc.code if isinstance(exc, SystemExit) and isinstance(exc.code, int) and exc.code else 1
        finally:
            faulthandler.disable()


if __name__ == '__main__':
    raise SystemExit(main())
