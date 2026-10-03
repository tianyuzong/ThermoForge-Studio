"""Versioned, source-labelled starting points for isotropic linear elasticity.

The material payload remains the existing schema: every assumption travels in
``source`` when a card is copied into a project, case or immutable job snapshot.
Catalogue metadata is deliberately outside that payload for legacy compatibility.
"""
from copy import deepcopy
from .schemas import Material, DEMO_MATERIAL, DEMO_ALUMINUM

REVISION = '2026-09-27.1'
ENSINGER = 'https://www.ensingerplastics.com/en-us/-/media/ensinger/files/document-teaser-files/brochures/shapes/engineering-plastics-manual-english.ashx'
CORE = 'https://www.outokumpu.com/-/media/files/products/core/outokumpu-core-range-datasheet.pdf'
SUPRA = 'https://www.outokumpu.com/-/media/files/products/supra/outokumpu-supra-range-datasheet.pdf'
STEEL = 'https://www.salzgitter-flachstahl.de/en/products/hot-rolled-products/steel-grades/non-alloy-structural-steels.html'
PLASTIC_SCOPE = '室温短时、小应变、各向同性线弹性近似；不包含蠕变、温湿度依赖、塑性或打印层间各向异性。拉伸屈服仅用于参考应力比，不能作为塑料长期承载合格结论。'
NU_ASSUMPTION = '泊松比为建模近似，非该厂家数据表实测值；使用前按实际材料复核。'
STEEL_ASSUMPTION = 'E=210 GPa、ν=0.30、ρ=7850 kg/m³ 为普通钢的建模近似；所列厂家来源仅支持该厚度范围的屈服下限。'


def _card(key, name, category, e_gpa, nu, density, yield_mpa, title, url,
          condition, aliases=(), assumptions=NU_ASSUMPTION, plastic=False):
    notes = ([PLASTIC_SCOPE] if plastic else ['室温、各向同性、小变形线弹性参考；需匹配实际牌号、供货状态及尺寸范围。'])
    notes += [assumptions]
    if yield_mpa is None:
        notes.append('未指定供货/加工状态的可靠屈服强度，保留为空；不输出屈服安全结论。')
    source = f'材料库 {REVISION}；{title}；{condition}；' + '；'.join(notes) + f' 来源：{url}'
    return {'id': 'builtin-' + key, 'builtin': True, 'category': category,
            'revision': REVISION, 'aliases': list(aliases), 'condition': condition,
            'notes': notes, 'references': [{'title': title, 'url': url}],
            'material': Material(name=name, young_modulus_Pa=e_gpa * 1e9,
                poisson_ratio=nu, density_kg_m3=density,
                yield_strength_Pa=None if yield_mpa is None else yield_mpa * 1e6,
                source=source).model_dump()}


def _plastic(key, name, category, e, nu, density, sy, aliases, condition='未增强型材的厂家典型值；实际批次、含水率与试验条件须复核。',
             title='Ensinger Engineering Plastics Manual · Material standard values', url=ENSINGER):
    return _card(key, name, category, e, nu, density, sy, title, url, condition, aliases, plastic=True)


_BUILTINS = [
    _plastic('abs-gp22', 'ABS · Terluran GP-22', '通用塑料', 2.3, .35, 1040, 45,
        ['ABS', '丙烯腈丁二烯苯乙烯', 'GP-22'], '注塑级，23 °C 典型拉伸数据；E=2300 MPa，拉伸屈服=45 MPa。',
        'INEOS Styrolution · Terluran GP-22', 'https://www.ineos-styrolution.com/Product/Terluran_Terluran-GP-22_SKU300600120827_lang_de_DE.html'),
    _plastic('pp-tecapro', 'PP · TECAPRO MT（热稳定）', '通用塑料', 2, .40, 930, 34,
        ['PP', '聚丙烯', 'TECAPRO MT'], '热稳定 PP 型材典型值；不是所有 PP 牌号的通用参数。'),
    _plastic('pe-robaflow', 'PE-HD · Robaflow PE 500', '通用塑料', 1.2, .42, 960, 27,
        ['PE', 'HDPE', '聚乙烯', '高密度聚乙烯', 'PE500'], 'Robaflow PE-HD / PE 500 技术数据表指导值，短时拉伸。',
        'Röchling · Robaflow 技术数据表', 'https://www.roechling.com/fileadmin/assets/Technical%20Data%20Sheet%20Robaflow%C2%AE%C2%A0%20709731%20EN.pdf'),
    _plastic('pvc-trovidur', 'PVC-U · Trovidur（白色硬 PVC）', '通用塑料', 3, .38, 1450, 45,
        ['PVC', 'PVC-U', '硬聚氯乙烯'], '未增塑硬 PVC 白色板材；不适用于软 PVC。',
        'Röchling · Trovidur PVC-U white', 'https://www.roechling.com/industrial/materials/thermoplastics/industrial-plastics/pvc-u/trovidur-pvc-u-white-591337'),
    _plastic('pc-tecanat', 'PC · TECANAT natural', '工程塑料', 2.2, .37, 1190, 69,
        ['PC', '聚碳酸酯', 'TECANAT']),
    _plastic('pcabs-t65xf', 'PC/ABS · Bayblend T65 XF', '工程塑料', 2.35, .37, 1130, 54,
        ['PC/ABS', 'PC+ABS', 'T65 XF'], '23 °C / 50% RH；拉伸模量 1 mm/min，拉伸屈服 50 mm/min。',
        'Covestro · Bayblend T65 XF', 'https://solutions.covestro.com/en/products/bayblend/bayblend-t65-xf_000000000006563805'),
    _plastic('pa6-tecamid', 'PA6 · TECAMID 6（尼龙）', '工程塑料', 3.3, .39, 1140, 78,
        ['PA6', 'PA 6', '尼龙6', '尼龙', 'TECAMID 6'], '未增强 PA6 型材典型值；吸湿会改变模量和强度，需按实际调湿状态复核。'),
    _plastic('pa66-tecamid', 'PA66 · TECAMID 66（尼龙）', '工程塑料', 3.5, .39, 1150, 84,
        ['PA66', 'PA 66', '尼龙66', '尼龙', 'TECAMID 66'], '未增强 PA66 型材典型值；吸湿会改变模量和强度，需按实际调湿状态复核。'),
    _plastic('pomc-ah', 'POM-C · TECAFORM AH（共聚甲醛）', '工程塑料', 2.8, .35, 1410, 67,
        ['POM', 'POM-C', '聚甲醛', '赛钢', 'TECAFORM AH']),
    _plastic('pomh-ad', 'POM-H · TECAFORM AD（均聚甲醛）', '工程塑料', 3.4, .35, 1430, 79,
        ['POM', 'POM-H', '聚甲醛', '赛钢', 'TECAFORM AD']),
    _plastic('pet-tecapet', 'PET · TECAPET natural', '工程塑料', 3.1, .38, 1360, 79,
        ['PET', '聚对苯二甲酸乙二醇酯', 'TECAPET']),
    _plastic('peek-tecapeek', 'PEEK · TECAPEEK natural', '高性能塑料', 4.2, .38, 1310, 116,
        ['PEEK', '聚醚醚酮', 'TECAPEEK']),
    _plastic('pei-tecapei', 'PEI · TECAPEI natural', '高性能塑料', 3.2, .37, 1280, 127,
        ['PEI', '聚醚酰亚胺', 'TECAPEI']),
    _plastic('psu-tecason', 'PSU · TECASON S', '高性能塑料', 2.7, .37, 1240, 89,
        ['PSU', '聚砜', 'TECASON S']),
    _plastic('pvdf-tecaflon', 'PVDF · TECAFLON PVDF', '高性能塑料', 2.2, .38, 1780, 62,
        ['PVDF', '聚偏氟乙烯', 'TECAFLON']),
    *[_card(key.lower(), f'{key} 结构钢（厚度 ≤16 mm）', '结构钢', 210, .3, 7850, sy,
        'Salzgitter Flachstahl · Non-alloy structural steels', STEEL,
        'EN 10025-2 热轧板带，公称厚度 ≤16 mm，屈服采用表列最低值。', [key, '碳钢', '结构钢'],
        STEEL_ASSUMPTION) for key, sy in [('S235JR', 235), ('S275JR', 275), ('S355J2', 355)]],
    *[_card(key, name, '不锈钢', 200, .3, 7900, sy,
        'Outokumpu · Core range datasheet', CORE,
        '20 °C；EN 10088-2 冷轧板带 C，Rp0.2 屈服下限。不是冷作硬化强度。', aliases)
      for key, name, sy, aliases in [
        ('ss304', '304 / 1.4301 不锈钢（冷轧板带）', 230, ['304', '1.4301', '不锈钢']),
        ('ss304l', '304L / 1.4307 不锈钢（冷轧板带）', 220, ['304L', '1.4307', '不锈钢'])]],
    _card('ss316l', '316L / 1.4404 不锈钢（ASTM A240）', '不锈钢', 200, .3, 8000, 170,
        'Outokumpu · Supra range datasheet', SUPRA,
        '20 °C；ASTM A240 板带参考，Rp0.2 下限 170 MPa。', ['316L', '1.4404', '不锈钢']),
    _card('al6061-t6', '6061-T6 铝合金（挤压型材）', '铝合金', 69, .33, 2700, 240,
        'Hydro · Alloy 6061 extrusion datasheet', 'https://www.hydro.com/globalassets/01-products--services/extruded-profiles/americas/ena-resources/alloy-data-sheets/hydro_2019_data_sheet_6061.pdf',
        'T6 挤压型材，屈服采用厂家 SI 表列下限 240 MPa；不能直接代替板材或其他热处理状态。',
        ['6061', '6061-T6', '铝合金'], 'E=69 GPa、ν=0.33、ρ=2700 kg/m³ 为该类铝合金的建模近似；厂家表用于屈服下限。'),
    _card('cu-c11000', '紫铜 C11000（屈服待指定状态）', '铜及铜合金', 17000 * .006894757293168, .328125, 8910, None,
        'Copper Development Association · C11000', 'https://alloys.copper.org/alloy/C11000',
        '室温弹性及密度参考；退火/硬态尚未指定。', ['铜', '紫铜', '纯铜', 'C11000'],
        'E 从 17000 ksi 换算；ν≈0.328 由 E=17000 ksi、G=6400 ksi 按各向同性关系推导；不表示厂家实测泊松比。'),
    _card('brass-c26000', '黄铜 C26000（屈服待指定状态）', '铜及铜合金', 16000 * .006894757293168, .34, 8530, None,
        'Copper Development Association · C26000', 'https://alloys.copper.org/alloy/C26000',
        '70/30 黄铜，室温弹性及密度参考；退火/硬态尚未指定。', ['黄铜', 'C26000', '70/30'],
        'E 从 16000 ksi 换算；' + NU_ASSUMPTION),
    *[{'id': key, 'builtin': True, 'category': '教学演示', 'revision': REVISION,
       'aliases': [], 'condition': '教学/软件验证假设，不代表实际认证材料。',
       'notes': [material['source']], 'references': [], 'material': material}
      for key, material in [('builtin-demo-steel', DEMO_MATERIAL), ('builtin-demo-aluminum', DEMO_ALUMINUM)]],
]


def builtin_materials():
    """Each consumer receives independent copies; saved cases cannot edit presets."""
    return deepcopy(_BUILTINS)


def material_library():
    from . import store
    return builtin_materials() + [dict(card, category=card.get('category', '我的材料'), builtin=False)
                                  for card in store.listing('material_card', 1000)]
