"""rerank 阈值校准入口（薄封装）。

真正的实现在 ``wealth_rag.calibration``——这样 CLI（``rag_tools calibrate``）与脚本共用同一份
逻辑，不会出现"两边算得不一样"。完整说明见该模块的文档字符串。

用法::

    # 1) 生成候选池（需先完成摄入）
    python eval/build_eval_pool.py --queries eval/queries.calibration.jsonl --out eval/pool

    # 2) 人工按 0/1/2 填写 eval/pool/labels.template.json → 另存为 labels.json

    # 3) 合并标注
    python eval/apply_grades.py --pool eval/pool --labels eval/pool/labels.json

    # 4) 校准阈值（--ab 额外跑 rerank 开/关的块级对比）
    python eval/tune_rerank_threshold.py --queries eval/queries.graded.jsonl --ab
"""

from wealth_rag.calibration import main

if __name__ == "__main__":
    main()
