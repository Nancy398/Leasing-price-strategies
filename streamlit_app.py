import streamlit as st
import requests
import pandas as pd
import numpy as np

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

merged_df = pd.merge(
    leases_df, 
    lark_df[['Room Number', 'Real Price', 'Monthly Concession', 'Lease Status']], 
    on='Room Number', 
    how='left'
)

merged_df['Real Price'] = pd.to_numeric(merged_df['Real Price'], errors='coerce').fillna(0)
merged_df['Monthly Concession'] = pd.to_numeric(merged_df['Monthly Concession'], errors='coerce').fillna(0)
merged_df['Net Rent'] = merged_df['Real Price'] - merged_df['Monthly Concession']
cost_df = pd.read_csv("Cost.csv")
property_df = pd.read_csv("Property.csv")
cost_df.columns = cost_df.columns.str.strip()
cost_cols = ['Mortgage Loan Interest', 'Insurance', 'Tax', 'Other Fixed']
for col in cost_cols:
    if col in cost_df.columns:
        # 先转成字符串，去掉逗号，再转成数字
        cost_df[col] = pd.to_numeric(
            cost_df[col].astype(str).str.replace(',', '').str.strip(), 
            errors='coerce'
        ).fillna(0)
cost_df['Total_Fixed'] = cost_df[cost_cols].sum(axis=1)
cost_summary = cost_df[['Property ID', 'Total_Fixed']]

signed_leases_df = merged_df[merged_df['Lease Status'] == 'Lease Signed'].copy()

leased_info = signed_leases_df.groupby('Property ID').agg(
    Leased_Units=('Room Number', 'count'),          # 这里统计的就是已签约的房间数了
    Already_Leased_Rev=('Net Rent', 'sum') # 这里统计的就是已签约的总净租金
).reset_index()

# 3. 然后再与 property_df 合并
final_df = property_df.merge(cost_summary, on='Property ID', how='left') \
                      .merge(leased_info, on='Property ID', how='left')

final_df['Leased_Units'] = final_df['Leased_Units'].fillna(0)
final_df['Already_Leased_Rev'] = final_df['Already_Leased_Rev'].fillna(0)

final_df['Vacant_Units'] = final_df['Total Unit'] - final_df['Leased_Units']
final_df['Variable_Rate'] = final_df['Type'].apply(lambda x: 0.12 if x == "MH" else 0.0)
final_df['Denominator'] = 1 - final_df['Variable_Rate']

final_df['Total_Commission'] = final_df['Total Unit'] * 50
final_df['Total_Required_Costs'] = final_df['Total_Fixed'] + final_df['Total_Commission']

final_df['Required_Total_Rev'] = final_df['Total_Required_Costs'] / final_df['Denominator']
final_df['Gap_To_Fill'] = final_df['Required_Total_Rev'] - final_df['Already_Leased_Rev']
final_df['Breakeven_Rent'] = np.where(
    final_df['Vacant_Units'] <= 0,
    0,
    final_df['Gap_To_Fill'] / final_df['Vacant_Units']
)
# Current Average Leased
final_df['Current_Avg_Leased'] = (
    final_df['Already_Leased_Rev'] / final_df['Leased_Units']
).fillna(0)

# 如果出现 Leased_Units 为 0 导致结果为无穷大 (inf)，可以进行修正
final_df['Current_Avg_Leased'] = final_df['Current_Avg_Leased'].replace([np.inf, -np.inf], 0)

#Occupancy
final_df['Occupancy %'] = (
    final_df['Leased_Units'] / final_df['Total Unit']
).fillna(0)

# 转换成百分比格式显示预览
# final_df['Occupancy %'] = final_df['Occupancy %'].clip(0, 1) # 确保不会超过 100%

# 如果计算出负数（说明现有租金已覆盖成本），通常置为 0
# final_df['Breakeven_Rent'] = final_df['Breakeven_Rent'].clip(lower=0)

##Target Price
target_profit_margin = st.sidebar.slider(
    "Target Profit Margin (%)", 
    min_value=0.0, 
    max_value=20.0, 
    value=5.0, 
    step=1.0
) / 100


def calculate_target_price(df, profit_margin):
    # VariableRate: IF(Type == "MH", 0.12, 0)
    df['Variable_Rate'] = df['Type'].apply(lambda x: 0.12 if x == "MH" else 0.0)
    df['Denominator'] = 1 - df['Variable_Rate'] - profit_margin
    
    # --- 2. 成本汇总 ---
    # TotalCommission = Total Unit * 50
    df['Total_Commission'] = df['Total Unit'] * 50
    # TotalRequiredCosts = TotalFixed + TotalCommission
    df['Total_Required_Costs'] = df['Total_Fixed'] + df['Total_Commission']
    
    # --- 3. 核心定价逻辑 ---
    # Required_Total_Rev = TotalRequiredCosts / Denominator
    # 处理 Denominator <= 0 的极端情况
    df['Required_Total_Rev'] = np.where(
        df['Denominator'] <= 0,
        np.nan, 
        df['Total_Required_Costs'] / df['Denominator']
    )
    
    # Gap_To_Fill = Required_Total_Rev - Already_Leased_Rev
    df['Gap_To_Fill'] = df['Required_Total_Rev'] - df['Already_Leased_Rev']
    
    # --- 4. 最终输出 ---
    # 逻辑判断：如果分母异常、如果没有空置房、或者计算结果
    conditions = [
        (df['Denominator'] <= 0),
        (df['Vacant_Units'] <= 0)
    ]
    choices = [
        np.nan, # 表示 Error: High Margin
        0       # 表示 Full / No Vacancy (或者你可以设为 0)
    ]
    
    df['Target_Remaining_Price'] = np.select(
        conditions, 
        choices, 
        default=df['Gap_To_Fill'] / df['Vacant_Units']
    )
    return df

# 执行计算
final_df = calculate_target_price(final_df, target_profit_margin)

final_df['Current_Avg_Leased'] = (
    final_df['Already_Leased_Rev'] / final_df['Leased_Units']
).fillna(0)

# 如果出现 Leased_Units 为 0 导致结果为无穷大 (inf)，可以进行修正
final_df['Current_Avg_Leased'] = final_df['Current_Avg_Leased'].replace([np.inf, -np.inf], 0)
final_df['Est_NOI'] = final_df['Already_Leased_Rev']-final_df['Total_Fixed']-final_df['Leased_Units']*50 - final_df['Already_Leased_Rev']*0.12

# 格式化展示
st.dataframe(
    final_df[['Property ID', 'Type', 'Vacant_Units', 'Breakeven_Rent', 'Target_Remaining_Price','Current_Avg_Leased','Est_NOI']],
    column_config={
        "Target_Remaining_Price": st.column_config.NumberColumn(
            "Target Price",
            format="$%.2f",
            help="为达到目标利润率，剩余空置房需收取的平均租金"
        )
    }
)
