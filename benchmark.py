"""AI Trader 性能基准测试
采集可写进简历的关键指标：
- 数据获取延迟（curl → 东方财富/新浪）
- 技术指标计算耗时
- RAG 向量检索延迟
- 工具注册表执行吞吐
- 单轮分析端到端耗时
- Agent 规划循环耗时
"""
import json
import logging
import statistics
import time
from datetime import datetime

logging.basicConfig(level=logging.WARNING)

# ========== 测试工具 ==========

def bench(name, func, iterations=5, warmup=1):
    """基准测试：多次执行取统计值"""
    # 预热
    for _ in range(warmup):
        try:
            func()
        except Exception:
            pass

    times = []
    errors = 0
    for i in range(iterations):
        start = time.perf_counter()
        try:
            result = func()
            elapsed = time.perf_counter() - start
            times.append(elapsed)
        except Exception as e:
            errors += 1
            elapsed = time.perf_counter() - start
            times.append(elapsed)

    if not times:
        return {"name": name, "error": "全部失败"}

    return {
        "name": name,
        "iterations": iterations,
        "errors": errors,
        "avg_ms": round(statistics.mean(times) * 1000, 1),
        "p50_ms": round(statistics.median(times) * 1000, 1),
        "p95_ms": round(sorted(times)[int(len(times) * 0.95)] * 1000, 1) if len(times) > 1 else round(times[0] * 1000, 1),
        "min_ms": round(min(times) * 1000, 1),
        "max_ms": round(max(times) * 1000, 1),
        "std_ms": round(statistics.stdev(times) * 1000, 1) if len(times) > 1 else 0,
    }


def bench_concurrent(name, func, concurrency=10, total=100):
    """并发基准测试（用线程池模拟）"""
    import concurrent.futures

    times = []
    errors = 0

    def worker():
        start = time.perf_counter()
        try:
            func()
            return time.perf_counter() - start, None
        except Exception as e:
            return time.perf_counter() - start, str(e)

    start_all = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker) for _ in range(total)]
        for f in concurrent.futures.as_completed(futures):
            t, err = f.result()
            times.append(t)
            if err:
                errors += 1
    total_time = time.perf_counter() - start_all

    return {
        "name": name,
        "concurrency": concurrency,
        "total_requests": total,
        "total_time_s": round(total_time, 2),
        "qps": round(total / total_time, 1),
        "avg_ms": round(statistics.mean(times) * 1000, 1),
        "p95_ms": round(sorted(times)[int(len(times) * 0.95)] * 1000, 1),
        "errors": errors,
    }


# ========== 测试项 ==========

def test_data_fetch():
    """数据获取性能"""
    import market_data as md
    results = []

    # 实时行情（单只）
    results.append(bench("实时行情-单只",
        lambda: md.get_realtime_quote('600519'), iterations=10, warmup=2))

    # 实时行情（批量5只）
    codes = ['600519', '300750', '601318', '000858', '000001']
    results.append(bench("实时行情-批量5只",
        lambda: [md.get_realtime_quote(c) for c in codes], iterations=5, warmup=1))

    # 历史K线
    results.append(bench("历史K线-120天",
        lambda: md.get_stock_history('600519', days=120), iterations=5, warmup=1))

    # 大盘概况
    results.append(bench("大盘概况",
        lambda: md.get_market_overview(), iterations=10, warmup=2))

    # 热门板块
    results.append(bench("热门板块",
        lambda: md.get_hot_sectors(), iterations=5, warmup=1))

    return results


def test_indicators():
    """技术指标计算性能"""
    import market_data as md

    df = md.get_stock_history('600519', days=120)

    results = []
    results.append(bench("技术指标计算",
        lambda: md.get_technical_indicators(df), iterations=20, warmup=3))

    return results


def test_rag():
    """RAG 向量检索性能"""
    try:
        from rag_store import RagStore
        store = RagStore()
        results = []

        # 确保有数据
        if store.decisions.count() == 0:
            print("  RAG 索引为空，先重建...")
            store.rebuild_index()

        results.append(bench("RAG-语义检索",
            lambda: store.search("MACD金叉买入", top_k=3), iterations=10, warmup=2))

        results.append(bench("RAG-股票专用检索",
            lambda: store.search_for_stock('600519', '贵州茅台', ['MACD金叉', 'RSI超卖']),
            iterations=10, warmup=2))

        # 集合统计
        results.append({
            "name": "RAG-索引规模",
            "decisions": store.decisions.count(),
            "predictions": store.predictions.count(),
            "lessons": store.lessons.count(),
        })

        return results
    except ImportError:
        return [{"name": "RAG", "error": "ChromaDB未安装"}]


def test_tool_registry():
    """工具注册表执行性能"""
    from agent_tools import create_default_registry
    registry = create_default_registry()

    results = []

    # 单工具执行
    results.append(bench("工具执行-get_watchlist",
        lambda: registry.execute("get_watchlist", {}), iterations=20, warmup=3))

    # TOOL_CALL 解析
    from agent_tools import parse_tool_calls
    test_text = 'TOOL_CALL: get_stock_history(code="600519", days=60)\nTOOL_CALL: get_technical_indicators(code="600519")'
    results.append(bench("TOOL_CALL解析",
        lambda: parse_tool_calls(test_text), iterations=100, warmup=10))

    return results


def test_rule_engine():
    """规则引擎性能"""
    import market_data as md
    import strategy

    df = md.get_stock_history('600519', days=120)
    indicators = md.get_technical_indicators(df)

    results = []
    results.append(bench("规则信号判断",
        lambda: strategy.rule_signal(indicators), iterations=100, warmup=10))

    # 批量扫描
    results.append(bench("股票池扫描-5只",
        lambda: __import__('stock_pool').scan_market(5), iterations=3, warmup=1))

    return results


def test_end_to_end():
    """端到端分析耗时"""
    import market_data as md
    import strategy
    import ai_analyzer

    results = []

    def single_stock_analysis():
        """单只股票完整分析流程"""
        df = md.get_stock_history('600519', days=120)
        indicators = md.get_technical_indicators(df)
        quote = md.get_realtime_quote('600519')
        overview = md.get_market_overview()
        signal = strategy.rule_signal(indicators)
        return signal

    results.append(bench("单股分析-规则层(无AI)",
        single_stock_analysis, iterations=3, warmup=1))

    # AI 分析（实际调 API，只测1次）
    def ai_analysis():
        df = md.get_stock_history('600519', days=120)
        indicators = md.get_technical_indicators(df)
        quote = md.get_realtime_quote('600519')
        return ai_analyzer.analyze_stock(
            stock_info=quote, indicators=indicators,
            market_overview=md.get_market_overview())

    results.append(bench("单股分析-AI层",
        ai_analysis, iterations=1, warmup=0))

    return results


# ========== 主程序 ==========

def run_all():
    print("=" * 60)
    print(f"AI Trader 性能基准测试 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    all_results = {}

    # 1. 数据获取
    print("\n[1/6] 数据获取性能...")
    all_results["data_fetch"] = test_data_fetch()

    # 2. 技术指标
    print("[2/6] 技术指标计算性能...")
    all_results["indicators"] = test_indicators()

    # 3. RAG
    print("[3/6] RAG 向量检索性能...")
    all_results["rag"] = test_rag()

    # 4. 工具注册表
    print("[4/6] 工具注册表性能...")
    all_results["tool_registry"] = test_tool_registry()

    # 5. 规则引擎
    print("[5/6] 规则引擎性能...")
    all_results["rule_engine"] = test_rule_engine()

    # 6. 端到端
    print("[6/6] 端到端分析性能...")
    all_results["end_to_end"] = test_end_to_end()

    # 打印结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    for category, results in all_results.items():
        print(f"\n## {category}")
        for r in results:
            if isinstance(r, dict) and 'avg_ms' in r:
                err_str = f" ({r['errors']} errors)" if r.get('errors') else ""
                print(f"  {r['name']}: avg={r['avg_ms']}ms p50={r.get('p50_ms', '?')}ms "
                      f"p95={r.get('p95_ms', '?')}ms min={r['min_ms']}ms max={r['max_ms']}ms{err_str}")
            elif isinstance(r, dict):
                print(f"  {r.get('name', '?')}: {json.dumps({k:v for k,v in r.items() if k != 'name'}, ensure_ascii=False)}")

    # 保存到文件
    output_file = f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存到: {output_file}")

    return all_results


if __name__ == "__main__":
    run_all()
