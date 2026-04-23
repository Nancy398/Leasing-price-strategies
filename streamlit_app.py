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
        st.error("[Sticker] token 是空的")
        return pd.DataFrame()

    headers = {"Authorization": f"Bearer {token}"}
    
    url = f"https://open.larksuite.com/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"
    res = requests.get(url, headers=headers)
    data = res.json()

    # [Sticker] 关键：token 无效时强制重新获取（绕过 cache）
    if data.get("msg") == "Invalid access token for authorization":
        st.warning("[Sticker] token 失效，重新获取中...")
        
        token = get_tenant_access_token()  # 再拿一次
        headers = {"Authorization": f"Bearer {token}"}
        res = requests.get(url, headers=headers)
        data = res.json()

    if data.get("code") == 0:
        items = data.get("data", {}).get("items", [])
        return pd.DataFrame([i["fields"] for i in items])
    else:
        st.error(f"[Sticker] 抓取失败: {data}")
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

def set_mgmt_rate(prop_type):
    if prop_type == "MH":
        return 0.12
    elif prop_type == "ML":
        return 0.02
    else:
        return 0.0

final_df['Variable_Rate'] = final_df['Type'].apply(set_mgmt_rate)
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



def calculate_target_price(df, profit_margin):
    # # VariableRate: IF(Type == "MH", 0.12, 0)
    # def get_mgmt_rate(row):
    #     if row['Type'] == "MH":
    #         return 0.12
    #     elif row['Type'] == "ML":
    #         return 0.02
    #     else:
    #         return 0.0
            
    # df['Variable_Rate'] = df.apply(get_mgmt_rate, axis=1)
    # df['Denominator'] = 1 - df['Variable_Rate']
    
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
final_df['Est_NOI'] = (
    final_df['Already_Leased_Rev'] - 
    final_df['Total_Fixed'] - 
    (final_df['Leased_Units'] * 50) - 
    (final_df['Already_Leased_Rev'] * final_df['Variable_Rate'])
)

# st.dataframe(final_df)
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
st.dataframe(final_df)
##----SHOW-----
##---- 数据处理逻辑保持你原有的部分不变，直到 SHOW 之前 ----

##---- SHOW (重构后的展示部分) -----
st.title("PROPERTY LEASING STRATEGY")

# 1. 侧边栏基础筛选：先选 ID
all_prop_ids = sorted(final_df['Property ID'].unique().tolist())
selected_id = st.sidebar.selectbox("Select Property ID", all_prop_ids)

# 获取选中物业的原始数据
current_prop_row = final_df[final_df['Property ID'] == selected_id].iloc[0]
current_company = current_prop_row['Company']

# 筛选该公司旗下所有物业 (用于 Whole 模式)
company_portfolio = final_df[final_df['Company'] == current_company].copy()
other_props_count = len(company_portfolio)

# 2. 视角切换逻辑
view_mode = "Single" 
if other_props_count > 1:
    st.sidebar.info(f"💡 该公司旗下共有 {other_props_count} 个物业")
    view_mode = st.sidebar.radio("切换分析视角:", ["Single", "Whole"], index=0)

# --- 核心数据适配层 ---
if view_mode == "Whole":
    st.subheader(f"🏢 {current_company} - 投资组合全景汇总")
    
    # 汇总地址显示
    all_addresses = company_portfolio['Address'].unique().tolist()
    address_display = " | ".join([f"**{addr}**" if addr == current_prop_row['Address'] else addr for addr in all_addresses])
    st.info(f"📍 关联地址: {address_display}")

    # 构造聚合后的 prop_data (将所有物业看作一个整体)
    agg_dict = {
        'Property Name': f"{current_company} Portfolio",
        'Total Unit': company_portfolio['Total Unit'].sum(),
        'Total_Fixed': company_portfolio['Total_Fixed'].sum(),
        'Leased_Units': company_portfolio['Leased_Units'].sum(),
        'Already_Leased_Rev': company_portfolio['Already_Leased_Rev'].sum(),
        'Vacant_Units': company_portfolio['Vacant_Units'].sum(),
        'Type': company_portfolio['Type'].iloc[0], # 费率基准取第一个
        'Variable_Rate': current_prop_row['Variable_Rate'],
        'Denominator': current_prop_row['Denominator']
    }
    prop_data = pd.Series(agg_dict)
    # 用于矩阵计算的 DataFrame 也是全组合
    matrix_df = company_portfolio
else:
    # Single 模式：直接使用选中的行
    st.subheader(f"🏠 {current_prop_row['Property Name']} ({selected_id})")
    st.markdown(f"📍 地址: **{current_prop_row['Address']}**")
    st.write(f"物业类型: {current_prop_row['Type']} | 公司: {current_prop_row['Company']}")
    
    prop_data = current_prop_row
    # 用于矩阵计算的 DataFrame 仅当前物业
    matrix_df = final_df[final_df['Property ID'] == selected_id]

# --- 3. 关键指标卡片 (无论哪种视角，计算逻辑复用) ---
st.write("---")
target_profit_pct = st.slider(
    "Set Target Margin (%)", 0.0, 20.0, 5.0, 1.0, key="margin_slider"
)

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("空置房间 (Vacant)", int(prop_data['Vacant_Units']))

with col2:
    # 重新计算该视角下的保本租金
    # Gap = (FixedCosts + Commission) / Denominator - AlreadyLeasedRev
    total_commission = prop_data['Total Unit'] * 50
    req_rev_be = (prop_data['Total_Fixed'] + total_commission) / prop_data['Denominator']
    be_rent = (req_rev_be - prop_data['Already_Leased_Rev']) / prop_data['Vacant_Units'] if prop_data['Vacant_Units'] > 0 else 0
    st.metric("保本租金 (Breakeven)", f"${max(0, be_rent):,.2f}")

with col3:
    # 预计当前总 NOI
    current_noi = (prop_data['Already_Leased_Rev'] * prop_data['Denominator']) - (prop_data['Leased_Units'] * 50) - prop_data['Total_Fixed']
    st.metric("预计总 NOI (Current)", f"${current_noi:,.0f}")

with col4:    
    # 计算目标租金
    target_margin = target_profit_pct / 100
    denom_with_margin = 1 - prop_data['Variable_Rate'] - target_margin
    
    if denom_with_margin > 0 and prop_data['Vacant_Units'] > 0:
        total_req_costs = prop_data['Total_Fixed'] + (prop_data['Total Unit'] * 50)
        req_rev_target = total_req_costs / denom_with_margin
        target_price = (req_rev_target - prop_data['Already_Leased_Rev']) / prop_data['Vacant_Units']
        st.metric("目标租金 (Target)", f"${max(0, target_price):,.2f}")
    else:
        st.metric("目标租金 (Target)", "N/A")

# --- 4. 出租率仪表盘 ---
st.write("---")
# 计算当前视角的总出租率
total_occ_rate = (prop_data['Leased_Units'] / prop_data['Total Unit'] * 100) if prop_data['Total Unit'] > 0 else 0

fig_gauge = go.Figure(go.Indicator(
    mode = "gauge+number",
    value = total_occ_rate,
    number = {'suffix': "%", 'font': {'color': "#1f77b4"}},
    title = {'text': "Occupancy Rate", 'font': {'size': 20, 'color': "#1f77b4"}},
    domain = {'x': [0, 1], 'y': [0, 1]},
    gauge = {
        'axis': {'range': [0, 100], 'tickwidth': 1, 'tickcolor': "#1f77b4"},
        'bar': {'color': "#003f5c"},
        'bgcolor': "white",
        'borderwidth': 1,
        'bordercolor': "#e0e0e0",
        'steps': [
            {'range': [0, 70], 'color': "#f0f4f8"},
            {'range': [70, 90], 'color': "#d1e3f0"},
            {'range': [90, 100], 'color': "#a3c1da"}
        ],
        'threshold': {
            'line': {'color': "#ff4b4b", 'width': 3},
            'thickness': 0.75,
            'value': 95}
    }
))
fig_gauge.update_layout(height=300, margin=dict(l=30, r=30, t=50, b=20), paper_bgcolor = "rgba(0,0,0,0)")
st.plotly_chart(fig_gauge, use_container_width=True)

# --- 5. 敏感性分析矩阵 ---
st.write("---")
st.subheader("Sensitivity Analysis (NOI)")

c1, c2 = st.columns(2)
with c1:
    r_range = st.slider("Rent Level", 400, 3000, (800, 2000), step=50, key="matrix_rent")
with c2:
    # 动态调整空置模拟范围，上限为当前视角下的总房间数
    max_vac_limit = int(prop_data['Total Unit'])
    v_range = st.slider("Vacancy Level", 0, max_vac_limit, (0, min(10, max_vac_limit)), key="matrix_vac")

rent_levels = np.arange(r_range[0], r_range[1] + 50, 100)
vac_levels = list(range(v_range[0], v_range[1] + 1))

# 这里传入 matrix_df，如果是 Whole 模式，它会计算该公司下所有物业的 NOI 模拟
noi_matrix = generate_dynamic_noi_matrix(matrix_df, rent_levels, vac_levels)

st.dataframe(
    noi_matrix.style.background_gradient(cmap='Blues', axis=None).format("${:,.0f}"),
    use_container_width=True
)

# 如果是 Whole 模式，最后加一个简单的物业对比清单
if view_mode == "Whole":
    st.write("---")
    st.subheader("Property Comparison Inside Portfolio")
    comparison_df = company_portfolio[['Property ID', 'Property Name', 'Occupancy %', 'Est_NOI']]
    st.dataframe(comparison_df.style.format({'Occupancy %': '{:.1%}', 'Est_NOI': '${:,.0f}'}), use_container_width=True)
