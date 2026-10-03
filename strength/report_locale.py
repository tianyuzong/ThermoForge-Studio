"""Chinese presentation vocabulary; machine identifiers and raw data stay intact."""
import re

STATUS = {
    'within_limit': '在已设限值内', 'exceeded': '超出限值', 'not_evaluated': '未评估',
    'not_verified': '未验证', 'converged': '达到本平台变化阈值', 'needs_review': '需要复核',
    'ok': '有效', 'partial': '部分完成', 'undefined': '无定义', 'outside': '模型外',
    'unavailable': '不可用', 'completed': '已完成', 'failed': '未完成',
}

TERMS = {
    'report-evidence.json': '报告计算依据文件', 'report-figures/*.png': '报告图像目录',
    'gpu-execution.json': '显卡执行记录文件', 'field-statistics.csv': '完整指标统计表',
    'result-queries.json': '结果查询记录文件', 'analysis.inp': '求解输入文件',
    'summary.json': '结果摘要文件', 'config.json': '工况配置文件',
    'bonding.json': '接口传力记录文件', 'contact.json': '接触计算记录文件',
    'metrics.csv': '指标汇总表', 'solver.log': '求解日志', 'fields.h5': '原始场数据文件',
    'calculix_cpu': '处理器求解', 'calculix_cuda': '显卡扩展求解', 'cudss_cuda': '显卡稀疏直接求解',
    'CUDA + cuDSS': '显卡稀疏直接求解', 'CUDA / cuDSS': '显卡稀疏直接求解',
    'von Mises': '米塞斯', 'Von Mises': '米塞斯', 'von_mises': '米塞斯等效应力',
    'Tresca': '特雷斯卡', 'tresca': '特雷斯卡最大剪应力',
    'Jacobian': '雅可比行列式', 'Hertz': '赫兹', 'linear static': '线弹性静力',
    'CalculiX': '开源有限元求解器', 'Ansys': '参考商业软件', 'Gmsh': '网格生成器',
    'STRENGTH STUDIO': '结构力学工作台', 'Strength Studio': '结构力学工作台',
    'ENGINEERING REPORT': '工程分析报告', 'Codex': '智能助手',
    'C3D10': '十节点二次四面体', 'PaStiX': '并行稀疏直接求解器',
    'cuDSS': '显卡稀疏直接求解库', 'CUDA': '显卡计算',
    'CPU': '处理器', 'GPU': '显卡', 'CAD': '几何模型',
    'PDF': '文档', 'CSV': '表格', 'SHA-256': '256位散列校验码',
    'FP64': '双精度浮点数', 'float64': '双精度浮点数',
    'nu=': '泊松比=', '面 ID': '面编号', '组件 ID': '组件编号',
    '附录 A': '附录一', '附录 B': '附录二',
    'Ensinger Engineering Plastics Manual · Material standard values': '厂家工程塑料手册 · 标准物性数据',
    'INEOS Styrolution · Terluran GP-22': '厂家 GP-22 材料技术资料',
    'Röchling · Robaflow 技术数据表': '厂家高密度聚乙烯技术数据表',
    'Röchling · Trovidur PVC-U white': '厂家白色硬聚氯乙烯技术资料',
    'Covestro · Bayblend T65 XF': '厂家 T65 XF 材料技术资料',
    'Salzgitter Flachstahl · Non-alloy structural steels': '厂家非合金结构钢技术资料',
    'Outokumpu · Core range datasheet': '厂家不锈钢系列技术数据表',
    'Outokumpu · Supra range datasheet': '厂家耐腐蚀不锈钢系列技术数据表',
    'Hydro · Alloy 6061 extrusion datasheet': '厂家六〇六一铝合金挤压型材技术数据表',
    'Copper Development Association': '铜业发展协会',
    'natural': '本色', 'SI 表列': '国际单位制表列', 'RH': '相对湿度',
    'N·m⁻¹': 'N/m', 'm·N⁻¹': 'm/N', '−': '-',
}
URL = re.compile(r'https?://[^\s<>；，。]+')


def report_text(value):
    """Localize platform terms, preserving source links, units and grade codes."""
    text = str(value)
    links = []
    def protect(match):
        links.append(match.group())
        return f'\ufff0{len(links)-1}\ufff1'
    text = URL.sub(protect, text)
    for source, target in sorted(TERMS.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(source, target)
    text = re.sub(r'(?<![A-Za-z0-9])C(\d+)(?![A-Za-z0-9])', r'组件\1', text)
    text = re.sub(r'(?<![A-Za-z0-9])V(?=\d)', '版本 ', text)
    text = STATUS.get(text, text)
    for i, link in enumerate(links):
        text = text.replace(f'\ufff0{i}\ufff1', link)
    return text


DOWNLOAD_NAMES = {
    'report.pdf': '结构分析报告.pdf', 'report.md': '文字分析报告.md',
    'metrics.csv': '指标汇总.csv', 'field-statistics.csv': '完整指标统计.csv',
    'comparison.csv': '工况对照.csv', 'result.zip': '完整计算记录.zip',
}


def download_name(name, identifier, task=False):
    localized = '多工况分析报告.pdf' if task and name == 'report.pdf' else DOWNLOAD_NAMES.get(name, name)
    stem, extension = localized.rsplit('.', 1)
    return f'{stem}-{identifier[:8]}.{extension}'
