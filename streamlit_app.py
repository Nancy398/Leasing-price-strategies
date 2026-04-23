import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go 
import plotly.express as px


# 1. 设置常量 (建议生产环境使用 st.secrets 或 环境变量)
APP_ID = st.secrets["Larksuite"]["APP_ID"]
APP_SECRET = st.secrets["Larksuite"]["APP_SECRET"]
APP_TOKEN = "Bu3QbY095aE5H1sdXtvjoRG4pjb"
TABLE_ID = "tbldXd7TSURHd0sI"
TABLE_ID_1 = "tblJ1I75LphH4suv"

# 2. 获取访问令牌 Tenant Access Token
# @st.cache_data(ttl=7000) # 缓存 token，避免频繁请求
def get_tenant_access_token():
    url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    r = requests.post(url, json=payload)
    return r.json().get("tenant_access_token")


def fetch_bitable_data(TABLE_ID):
    token = get_tenant_access_token()

    if not token:
        st.error("❌ token 是空的")
        return pd.DataFrame()

    headers = {"Authorization": f"Bearer {token}"}
    
    url = f"https://open.larksuite.com/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"
    res = requests.get(url, headers=headers)
    data = res.json()

    # 🔥 关键：token 无效时强制重新获取（绕过 cache）
    if data.get("msg") == "Invalid access token for authorization":
        st.warning("⚠️ token 失效，重新获取中...")
        
        token = get_tenant_access_token()  # 再拿一次
        headers = {"Authorization": f"Bearer {token}"}
        res = requests.get(url, headers=headers)
        data = res.json()

    if data.get("code") == 0:
        items = data.get("data", {}).get("items", [])
        return pd.DataFrame([i["fields"] for i in items])
    else:
        st.error(f"❌ 抓取失败: {data}")
        return pd.DataFrame()
        
leases_df = pd.read_csv("Leases.csv")
lark_df_USC = fetch_bitable_data(TABLE_ID) 
lark_df_UCLA = fetch_bitable_data(TABLE_ID_1)

lark_df_UCLA = lark_df_UCLA[[
    "Unit - Room Number",
    "Rental Price",
    "Lease Status"
]].copy()

lark_df_UCLA = lark_df_UCLA.rename(columns={
    "Unit - Room Number": "Room Number",
    "Rental Price": "Real Price"
})

lark_df_UCLA["Monthly Concession"] = 0

lark_df_USC = lark_df_USC[[
    'Room Number',
    'Real Price',
    'Lease Status',
    'Monthly Concession'
]].copy()
lark_df_UCLA = lark_df_UCLA[[
    'Room Number',
    'Real Price',
    'Monthly Concession',
    'Lease Status'
]].copy()

lark_df = pd.concat([lark_df_USC, lark_df_UCLA], ignore_index=True)

leases_df['Room Number'] = leases_df['Room Number'].astype(str).str.strip()
lark_df['Room Number'] = lark_df['Room Number'].astype(str).str.strip()

merged_df = pd.merge(
    leases_df, 
    lark_df[['Room Number', 'Real Price','Lease Status', 'Monthly Concession']], 
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
final_df['Total_Fixed'] = final_df['Total_Fixed']+final_df['Total Unit']*30
final_df['Vacant_Units'] = final_df['Total Unit'] - final_df['Leased_Units']
final_df['Total_Commission'] = final_df['Total Unit'] * 50
def set_mgmt_rate(prop_type):
        if prop_type == "MH":
            return 0.12
        elif prop_type == "ML":
            return 0.02
        else:
            return 0.0    
final_df['Variable_Rate'] = final_df['Type'].apply(set_mgmt_rate)

def calculate(df):
    df['Denominator'] = 1 - df['Variable_Rate']
    df['Total_Required_Costs'] = df['Total_Fixed'] + df['Total_Commission']
    
    df['Required_Total_Rev'] = df['Total_Required_Costs'] / df['Denominator']
    df['Gap_To_Fill'] = df['Required_Total_Rev'] - df['Already_Leased_Rev']
    df['Breakeven_Rent'] = np.where(
        df['Vacant_Units'] <= 0,
        0,
        df['Gap_To_Fill'] / df['Vacant_Units']
    )
    # Current Average Leased
    df['Current_Avg_Leased'] = (
        df['Already_Leased_Rev'] / df['Leased_Units']
    ).fillna(0)
    
    # 如果出现 Leased_Units 为 0 导致结果为无穷大 (inf)，可以进行修正
    df['Current_Avg_Leased'] = df['Current_Avg_Leased'].replace([np.inf, -np.inf], 0)
    
    #Occupancy
    df['Occupancy %'] = (
        df['Leased_Units'] / df['Total Unit']
    ).fillna(0)
    
    
    
    def calculate_target_price(df, profit_margin):
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
            df['Total_Required_Costs'] * (1+ profit_margin)/ df['Denominator']
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
    
    
    final_df['Current_Avg_Leased'] = (
        final_df['Already_Leased_Rev'] / final_df['Leased_Units']
    ).fillna(0)
    
    # 如果出现 Leased_Units 为 0 导致结果为无穷大 (inf)，可以进行修正
    df['Est_NOI'] = (
        df['Already_Leased_Rev'] - 
        df['Total_Fixed'] - 
        (df['Leased_Units'] * 50) - 
        (df['Already_Leased_Rev'] * df['Variable_Rate'])
    )
    return df
    
def generate_dynamic_noi_matrix(df, rent_levels, vac_levels):
    # 1. 基础静态数据（这些是基于当前现状，不会随矩阵模拟改变）
    total_units = df['Total Unit'].sum()
    current_leased_count = df['Leased_Units'].sum()
    active_leased_rev = df['Already_Leased_Rev'].sum()  # 已经租出去的房子的总收入
    total_fixed_base_cost = df['Total_Fixed'].sum()
        
    # 提取固定成本中不随出租数变化的部分
    other_fixed_cost = total_fixed_base_cost
        
     # # 2. 确定管理费率 (如果是 MH 类型则为 12%)
    def get_matrix_mgmt_rate(prop_type):
        if prop_type == "MH":
            return 0.12
        elif prop_type == "ML":
            return 0.02
        else:
            return 0.0
    
        # 兼容处理：如果是 Series 拿第一个值，如果是字符串直接用
    p_type = df['Type'].iloc[0] if isinstance(df['Type'], pd.Series) else df['Type']
    mgmt_rate = get_matrix_mgmt_rate(p_type)
    
    matrix_data = []
        
        # 3. 开始模拟
    for rent in rent_levels:
        # 格式化 Rent 显示，增加逗号
        row = {"Rent": f"${rent:,.0f}"} 
            
        for vac in vac_levels:
            max_available = total_units - current_leased_count
            new_leased_count = max(max_available - vac, 0)
            total_leased_total = current_leased_count + new_leased_count
            total_rev = active_leased_rev + (new_leased_count * rent)
            noi = (total_rev * (1 - mgmt_rate)) - (total_leased_total * 50) - other_fixed_cost
            row[f"Vacant: {vac}"] = noi
        matrix_data.append(row) 
    return pd.DataFrame(matrix_data).set_index("Rent")

##----SHOW-----
##----SHOW-----
st.title("PROPERTY LEASING STRATEGY")

all_prop_ids = sorted(final_df['Property ID'].unique().tolist())
prop_id = st.sidebar.selectbox("Select Property ID", all_prop_ids)

# 获取当前物业的基础数据
current_prop_row = final_df[final_df['Property ID'] == prop_id].iloc[0]
current_company = current_prop_row['Company']

# 获取同公司组合
company_portfolio = final_df[final_df['Company'] == current_company].copy()
other_props_count = len(company_portfolio)

# 视角选择
view_mode = "Single"
if other_props_count > 1:
    st.sidebar.info(f"💡 该公司旗下共有 {other_props_count} 个物业")
    view_mode = st.sidebar.radio("分析视角:", ["Single", "Whole"], index=0)

# --- 核心数据准备 ---
if view_mode == "Whole":
    st.title(f"🏢 {current_company} Portfolio")
    
    # 汇总所有地址
    all_addresses = company_portfolio['Address'].unique().tolist()
    address_display = " | ".join([f"**{addr}**" if addr == current_prop_row['Address'] else addr for addr in all_addresses])
    st.info(f"📍 组合地址: {address_display}")

    # 1. 构造汇总后的 Series (agg_data)
    # 注意：我们这里手动汇总，然后转成 Series 以适配后面的计算公式
    agg_dict = {
        'Property Name': "Whole Portfolio",
        'Company': current_company,
        'Type': company_portfolio['Type'].iloc[0], # 假设费率以第一个为准，或逻辑自定义
        'Total Unit': company_portfolio['Total Unit'].sum(),
        'Total_Fixed': company_portfolio['Total_Fixed'].sum(),
        'Leased_Units': company_portfolio['Leased_Units'].sum(),
        'Already_Leased_Rev': company_portfolio['Already_Leased_Rev'].sum(),
        'Vacant_Units': company_portfolio['Vacant_Units'].sum(),
        'Total_Commission': company_portfolio['Total Unit'].sum() * 50
    }
    prop_data = pd.Series(agg_dict)
    
    # 2. 计算动态字段
    # 重新计算管理费率 (如果是汇总，建议取平均或指定)
    prop_data['Variable_Rate'] = 0.12 if prop_data['Type'] == "MH" else (0.02 if prop_data['Type'] == "ML" else 0.0)
    prop_data['Denominator'] = 1 - prop_data['Variable_Rate']
    prop_data['Occupancy %'] = prop_data['Leased_Units'] / prop_data['Total Unit'] if prop_data['Total Unit'] > 0 else 0
    
    # 矩阵分析的对象是整个组合
    matrix_df = company_portfolio

else:
    # --- Single 视角 ---
    st.title(f"🏠 {current_prop_row['Property Name']} ({prop_id})")
    st.markdown(f"### 📍 地址: {current_prop_row['Address']}")
    
    # 运行一次计算逻辑填充字段
    # 注意：为了不报错，我们将这一行转成 DataFrame 算完再拿出来
    temp_df = calculate(final_df[final_df['Property ID'] == prop_id].copy())
    prop_data = temp_df.iloc[0]
    
    # 矩阵分析的对象只是这一个物业
    matrix_df = final_df[final_df['Property ID'] == prop_id]

# --- 共享显示逻辑 (UI 统一) ---

# 1. 利润控制滑轨
target_profit_pct = st.slider("Set Target Margin (%)", 0.0, 20.0, 5.0, 1.0, key="margin_slider")

# 2. 关键指标卡片
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("Vacant Units", int(prop_data['Vacant_Units']))

with c2:
    # 保本租金计算
    total_costs = prop_data['Total_Fixed'] + prop_data['Total_Commission']
    breakeven_total_rev = total_costs / prop_data['Denominator']
    gap = breakeven_total_rev - prop_data['Already_Leased_Rev']
    be_rent = gap / prop_data['Vacant_Units'] if prop_data['Vacant_Units'] > 0 else 0
    st.metric("Breakeven Rent", f"${max(0, be_rent):,.2f}")

with c3:
    # 预计当前 NOI
    current_noi = (prop_data['Already_Leased_Rev'] * prop_data['Denominator']) - (prop_data['Leased_Units'] * 50) - prop_data['Total_Fixed']
    st.metric("Est. NOI (Current)", f"${current_noi:,.0f}")

with c4:
    # 目标租金计算 (包含 Margin)
    margin = target_profit_pct / 100
    denom_with_margin = 1 - prop_data['Variable_Rate'] - margin
    
    if denom_with_margin > 0 and prop_data['Vacant_Units'] > 0:
        total_costs = prop_data['Total_Fixed'] + prop_data['Total_Commission']
        target_rev = total_costs / denom_with_margin
        target_price = (target_rev - prop_data['Already_Leased_Rev']) / prop_data['Vacant_Units']
        st.metric("Target Rent", f"${max(0, target_price):,.2f}")
    else:
        st.metric("Target Rent", "N/A")

# 3. 仪表盘
st.write("---")
occ_rate = float(prop_data['Occupancy %']) * 100
# ... (此处放你之前的 go.Figure 仪表盘代码，使用 occ_rate 变量) ...
st.plotly_chart(fig_gauge, use_container_width=True)

# 4. 敏感性分析
st.write("---")
st.subheader("Sensitivity Analysis (NOI)")
r_min, r_max = st.slider("Rent Range", 400, 3000, (800, 2000), 50)
v_max = int(prop_data['Vacant_Units']) if prop_data['Vacant_Units'] > 1 else 5
v_range = st.slider("Vacancy Range", 0, v_max, (0, min(5, v_max)))

rent_levels = np.arange(r_min, r_max + 50, 100)
vac_levels = list(range(v_range[0], v_range[1] + 1))

# 传入刚才选定的 matrix_df (可能是单体，也可能是整个 Portfolio)
noi_matrix = generate_dynamic_noi_matrix(matrix_df, rent_levels, vac_levels)
st.dataframe(noi_matrix.style.background_gradient(cmap='Blues', axis=None).format("${:,.0f}"), use_container_width=True)
