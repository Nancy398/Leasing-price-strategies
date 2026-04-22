import streamlit as st
import requests
import pandas as pd

# 1. 设置常量 (建议生产环境使用 st.secrets 或 环境变量)
APP_ID = st.secrets["Larksuite"]["APP_ID"]
APP_SECRET = st.secrets["Larksuite"]["APP_SECRET"]
APP_TOKEN = "Bu3QbY095aE5H1sdXtvjoRG4pjb"
TABLE_ID = "tbldXd7TSURHd0sI"

# 2. 获取访问令牌 Tenant Access Token
@st.cache_data(ttl=7200) # 缓存 token，避免频繁请求
def get_tenant_access_token():
    url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    r = requests.post(url, json=payload)
    return r.json().get("tenant_access_token")

# 3. 抓取多维表格数据
def fetch_bitable_data():
    token = get_tenant_access_token()
    url = f"https://open.larksuite.com/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}"}
    
    # 注意：如果数据量大，需要处理分页 (page_token)
    res = requests.get(url, headers=headers)
    data = res.json()
    
    if data.get("code") == 0:
        items = data.get("data", {}).get("items", [])
        # 提取每一行的 fields 内容
        flat_data = [item['fields'] for item in items]
        return pd.DataFrame(flat_data)
    else:
        st.error(f"抓取失败: {data.get('msg')}")
        return pd.DataFrame()

leases_df = pd.read_csv("Leases.csv")
lark_df = fetch_bitable_data() 

leases_df['Room Number'] = leases_df['Room Number'].astype(str).str.strip()
lark_df['Room Number'] = lark_df['Room Number'].astype(str).str.strip()
st.dataframe(leases_df)
st.dataframe(lark_df)

merged_df = pd.merge(
    leases_df, 
    lark_df[['Room Number', 'Real Price', 'Monthly Concession', 'Lease Status']], 
    on='room number', 
    how='left'
)
st.subheader("合并后的数据看板")
st.dataframe(merged_df)
st.title("Lark 多维表格数据自动抓取")

# if st.button('刷新数据'):
#     df = fetch_bitable_data()
#     st.session_state['data'] = df

# if 'data' in st.session_state:
#     st.write("最新数据：")
#     st.dataframe(st.session_state['data'])
