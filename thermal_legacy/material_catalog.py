"""Constant-property starting points; sources and assumptions travel with each case."""
ENSINGER = 'https://www.ensingerplastics.com/-/media/ensinger/files/document-teaser-files/brochures/shapes/food-technology-shapes-en.ashx?rev=-1'
CORNING = 'https://www.corning.com/media/worldwide/csm/documents/Soda_Borosilicate_7740.pdf'
COORSTEK = 'https://www2.coorstek.com/media/8909/advanced-alumina-semiconductor-specific-02022.pdf'
CATEGORIES = dict(metal='金属', polymer='高分子', glass='玻璃', ceramic='陶瓷', elastomer='弹性体', composite='复合材料', other='其他/未分类')


def polymer(name, k, rho, cp, cte, modulus, strength, tg, service):
    return dict(name=name+'（参考示例）', category='polymer', k=k, rho=rho, cp=cp,
        thermal_expansion_CTE_per_K=cte*1e-6, young_modulus_Pa=modulus*1e9,
        poisson_ratio=.35, yield_strength_Pa=strength*1e6, strength_criterion='von_mises',
        glass_transition_C=tg, service_max_C=service, reference_temperature_C=23,
        data_source=ENSINGER,
        property_notes='Ensinger材料表第26–27页；热膨胀取23–60°C区间值。泊松比0.35为演示假设，须由牌号实测替换；其他值为该牌号典型值而非保证值。最高使用温度是厂商长期使用参考，不是恒定物性的有效温区；湿度、载荷、时间及温度会改变高分子性能。Tg不等同熔点或通用失效温度。')


NONMETAL_PRESETS = [
    polymer('POM-C 聚甲醛 TECAFORM AH', .39, 1410, 1400, 130, 2.8, 67, -60, 100),
    polymer('PA6 尼龙 TECAMID 6', .37, 1140, 1600, 120, 3.3, 78, 45, 100),
    polymer('PC 聚碳酸酯 TECANAT', .25, 1190, 1300, 80, 2.2, 69, 149, 120),
    polymer('PEEK 聚醚醚酮 TECAPEEK', .27, 1310, 1100, 50, 4.2, 116, 150, 260),
    polymer('PVDF 聚偏氟乙烯 TECAFLON', .25, 1780, 1300, 160, 2.2, 62, -40, 150),
    dict(name='硼硅玻璃 Corning 7740（参考示例）',category='glass',k=1.12968,rho=2230,cp=753.12,
         thermal_expansion_CTE_per_K=3.25e-6,young_modulus_Pa=62.76256e9,poisson_ratio=.2,
         strength_criterion='principal',reference_temperature_C=25,data_source=CORNING,
         property_notes='Corning 7740数据表；k、cp按25°C值，CTE按0–300°C平均值。已将cal及kgf/mm²换算为SI。未提供拉压强度，不能据此判定抗裂；表面缺陷、尺寸和加工状态需另行验证。'),
    dict(name='氧化铝陶瓷 AD-995（参考示例）',category='ceramic',k=30,rho=3900,cp=800,
         thermal_expansion_CTE_per_K=8.2e-6,young_modulus_Pa=370e9,poisson_ratio=.22,
         compressive_strength_Pa=2600e6,strength_criterion='principal',reference_temperature_C=25,data_source=COORSTEK,
         property_notes='CoorsTek AD-995典型值；E取370–380GPa范围下端；k、cp在25°C，CTE为室温–1000°C平均值。弯曲强度未冒充拉伸强度，抗裂仍未评估。常物性模型不代表该完整温区的精度。'),
]

# Specific nonmetal aliases precede metal substrings (e.g. 氧化铝 must not select 铝).
NONMETAL_ALIASES = [(r'聚甲醛|(?<![A-Za-z0-9])POM(?:-C)?(?![A-Za-z0-9])|TECAFORM',0),
    (r'尼龙|聚酰胺|(?<![A-Za-z0-9])PA\s*6(?![A-Za-z0-9])|TECAMID',1),
    (r'聚碳酸酯|(?<![A-Za-z0-9])PC(?![A-Za-z0-9])|TECANAT',2),
    (r'聚醚醚酮|(?<![A-Za-z0-9])PEEK(?![A-Za-z0-9])|TECAPEEK',3),
    (r'聚偏氟乙烯|(?<![A-Za-z0-9])PVDF(?![A-Za-z0-9])',4),
    (r'硼硅玻璃|硼硅酸盐玻璃|7740',5),
    (r'氧化铝(?:陶瓷)?|AD[- ]?995|alumina',6)]
