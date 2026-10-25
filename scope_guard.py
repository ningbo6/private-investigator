"""Conservative local contest scope gate; no user input leaves before this check."""
import re

BRANDS = {'腾讯', '小米', '宁德时代', '比亚迪', '华为', '阿里巴巴', '百度', '网易', '美团', '京东', '字节跳动', '苹果', '微软', '特斯拉', 'tencent', 'xiaomi', 'catl', 'apple', 'microsoft', 'tesla', 'huawei'}
ORG = re.compile(r'公司|集团|企业|科技|股份|银行|汽车|电子|游戏|餐饮|食品|制造|工业|网络|能源|生物|医药|商店|超市|研究院|工作室|品牌|\b(?:inc|ltd|limited|corporation|company|group)\b', re.I)


def check(company, question, aliases):
    text = ' '.join([company, question] + aliases)
    if re.search(r'时政|选举|政党|政治立场|国家领导人|总统|总理|政治局|两岸政治|军事冲突', text):
        raise ValueError('本作品只核查企业公开业务信息，不处理时政问题。请改为产品、经营、技术或企业合规问题。')
    if re.search(r'身份证|家庭住址|手机号|手机号码|私人电话|婚姻|婚外情|情史|绯闻|人肉|开盒|行踪|个人隐私|个人征信|个人履历|个人背景|个人消息|个人财产|家庭成员|老婆|老公|子女|配偶', text):
        raise ValueError('不调查个人信息或私人生活；请改为不涉及个人的企业公开事实。')
    if re.search(r'董事长|创始人|实控人|老板|员工姓名|高管个人|CEO|ceo|总裁|董事个人', text):
        raise ValueError('赛道不涉及个人消息。本次请改为公司层面的治理制度或公开业务，不调查具体人员。')
    for name in [company] + aliases:
        if name.lower() not in BRANDS and not ORG.search(name):
            raise ValueError('无法确认输入是企业而非个人，请填写包含“公司／集团／科技”等组织标识的工商全称；暂不检索该输入。')
    # Contact data is not needed for company fact checking.
    if re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)\d{17}[\dXx](?!\d)', text):
        raise ValueError('输入包含疑似个人联系方式或身份号码，请删除后再调查企业公开信息。')
