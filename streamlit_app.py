import streamlit as st
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go 
import plotly.express as px

# --- 样式注入：将 Slider 和其标题改为蓝色调 ---
DEEP_BLUE = "#003366" 

st.markdown(f"""
    <style>
    /* 1. 标题颜色 - 所有的 h1, h2, h3 */
    h1, h2, h3 {{
        color: {DEEP_BLUE} !important;
    }}

    /* 2. Metric 数字颜色 */
    [data-testid="stMetricValue"] {{
        color: {DEEP_BLUE} !important;
    }}
    
    /* 3. Metric 标签颜色 */
    [data-testid="stMetricLabel"] p {{
        color: {DEEP_BLUE} !important;
        font-weight: bold;
    }}

    /* 4. Slider 样式：轨道、圆钮和标题 */
    /* 标题 */
    .stSlider label {{
        color: {DEEP_BLUE} !important;
    }}
    /* 已滑动轨道颜色 */
    .stSlider [data-baseweb="slider"] div[style*="background-color"] {{
        background-color: {DEEP_BLUE} !important;
    }}
    /* 圆钮颜色 */
    .stSlider [role="slider"] {{
        background-color: {DEEP_BLUE} !important;
        border-color: {DEEP_BLUE} !important;
    }}
    /* 针对较新版本的轨道颜色覆盖 */
    .stSlider [data-baseweb="slider"] > div > div {{
        background: {DEEP_BLUE} !important;
    }}

    /* 5. 分割线颜色 */
    hr {{
        border-top: 2px solid {DEEP_BLUE};
    }}
    </style>
    """, unsafe_allow_html=True)

# 1. 设置常量 (建议生产环境使用 st.secrets 或 环境变量)
APP_ID = st.secrets["Larksuite"]["APP_ID"]
APP_SECRET = st.secrets["Larksuite"]["APP_SECRET"]
APP_TOKEN = "Bu3QbY095aE5H1sdXtvjoRG4pjb"
TABLE_ID = "tbldXd7TSURHd0sI"

# 2. 获取访问令牌 Tenant Access Token
@st.cache_data(ttl=7000) # 缓存 token，避免频繁请求
def get_tenant_access_token():
    url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    r = requests.post(url, json=payload)
    return r.json().get("tenant_access_token")


def fetch_bitable_data():
    token = get_tenant_access_token()
    
    url = f"https://open.larksuite.com/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}"}
    
    res = requests.get(url, headers=headers)
    data = res.json()

    # ✅ 如果 token 失效，自动重试一次
    if data.get("code") in [99991663, 99991661]:  # token 相关错误
        token = get_tenant_access_token()  # 重新拿
        headers = {"Authorization": f"Bearer {token}"}
        res = requests.get(url, headers=headers)
        data = res.json()

    if data.get("code") == 0:
        items = data.get("data", {}).get("items", [])
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

# ##Target Price
# target_profit_margin = st.sidebar.slider(
#     "Target Profit Margin (%)", 
#     min_value=0.0, 
#     max_value=20.0, 
#     value=5.0, 
#     step=1.0
# ) / 100


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
# final_df = calculate_target_price(final_df, target_profit_margin)

final_df['Current_Avg_Leased'] = (
    final_df['Already_Leased_Rev'] / final_df['Leased_Units']
).fillna(0)

# 如果出现 Leased_Units 为 0 导致结果为无穷大 (inf)，可以进行修正
final_df['Current_Avg_Leased'] = final_df['Current_Avg_Leased'].replace([np.inf, -np.inf], 0)
final_df['Est_NOI'] = final_df['Already_Leased_Rev']-final_df['Total_Fixed']-final_df['Leased_Units']*50 - final_df['Already_Leased_Rev']*0.12

# # 格式化展示
# st.dataframe(
#     final_df[['Property ID', 'Type', 'Vacant_Units', 'Breakeven_Rent','Current_Avg_Leased','Est_NOI']],
#     column_config={
#         "Target_Remaining_Price": st.column_config.NumberColumn(
#             "Target Price",
#             format="$%.2f",
#             help="为达到目标利润率，剩余空置房需收取的平均租金"
#         )
#     }
# )

##----Sensitivity Analysis-----
st.header("NOI 敏感性模拟器")

# --- 装置 1: 选择租金范围 ---
# 使用 select_slider 或 slider 的范围模式 (value 传入元组)
rent_min, rent_max = st.slider(
    "选择模拟租金范围 ($)",
    min_value=800,
    max_value=2000,
    value=(800, 1500), # 默认范围
    step=50
)

# --- 装置 2: 选择空置数范围 ---
vac_min, vac_max = st.slider(
    "选择模拟空置房数范围",
    min_value=0,
    max_value=30, # 建议设为总房间数的最大值
    value=(0, 10), # 默认显示 0 到 10 个空置
    step=1
)

# 生成动态的范围数组
rent_levels = np.arange(rent_min, rent_max + 50, 100) # 每 100 一个档位
vac_levels = list(range(vac_min, vac_max + 1))

def generate_dynamic_noi_matrix(df, rent_levels, vac_levels):
    total_units = df['Total Unit'].sum()
    current_leased_count = df['Leased_Units'].sum()
    active_leased_rev = df['Already_Leased_Rev'].sum()
    total_fixed_base_cost = df['Total_Fixed'].sum()
    
    other_fixed_cost = total_fixed_base_cost - (total_units * 50)
    max_available = total_units - current_leased_count

    matrix_data = []
    for rent in rent_levels:
        row = {"租金水平": f"${rent}"}
        for vac in vac_levels:
            # 模拟逻辑
            new_leased = max(max_available - vac, 0)
            total_leased = current_leased_count + new_leased
            total_rev = active_leased_rev + (new_leased * rent)
            
            # 成本逻辑
            mgmt_rate = 0.12 if (df['Type'] == 'MH').any() else 0.0
            noi = (total_rev * (1 - mgmt_rate)) - (total_leased * 50) - other_fixed_cost
            
            row[f"空置 {vac}"] = noi
        matrix_data.append(row)
    
    return pd.DataFrame(matrix_data).set_index("租金水平")

# 计算矩阵
noi_matrix = generate_dynamic_noi_matrix(final_df, rent_levels, vac_levels)

st.subheader(f"NOI 模拟矩阵 (租金 {rent_min}-{rent_max} | 空置 {vac_min}-{vac_max})")

# 自动为所有动态生成的列配置货币格式
cols_config = {
    col: st.column_config.NumberColumn(format="$%.0f") 
    for col in noi_matrix.columns
}

st.dataframe(
    noi_matrix,
    column_config=cols_config,
    use_container_width=True
)

##----SHOW-----
st.title("PROPERTY LEASING STRATEGY")

# 假设 final_df 是你之前合并好并完成计算的 DataFrame
prop_id = st.selectbox("选择物业 ID (Property ID)", options=final_df['Property ID'].unique())

# 获取选中物业的数据行
prop_data = final_df[final_df['Property ID'] == prop_id].iloc[0]

# 展示地址
st.markdown(f"### 📍 地址: {prop_data['Address']}")
st.write(f"物业类型: {prop_data['Type']} | 公司: {prop_data['Company']}")

# --- 2. 关键指标卡片 ---
target_profit_pct = st.slider(
    "Set Margin (%)", 0.0, 20.0, 5.0, 1.0, key="margin_slider"
)
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("空置房间 (Vacant)", int(prop_data['Vacant_Units']))

with col2:
    st.metric("保本租金 (Breakeven)", f"${prop_data['Breakeven_Rent']:.2f}")

with col3:
    # 这里的 Est_NOI 可以是当前状态下的 NOI
    # 逻辑: (Already_Leased_Rev * (1-MgmtRate)) - (LeasedUnits * 50) - FixedCost
    st.metric("预计 NOI (Current)", f"${prop_data['Already_Leased_Rev'] - prop_data['Total_Fixed']:,.0f}")

with col4:    
    # B. 在这里执行计算逻辑 (不要在外面，就在这里算)
    target_margin = target_profit_pct / 100
    denominator = 1 - prop_data['Variable_Rate'] - target_margin
    
    if denominator > 0 and prop_data['Vacant_Units'] > 0:
        total_req_costs = prop_data['Total_Fixed'] + (prop_data['Total Unit'] * 50)
        req_rev = total_req_costs / denominator
        target_price = (req_rev - prop_data['Already_Leased_Rev']) / prop_data['Vacant_Units']
    else:
        target_price = 0  # 或者显示 np.nan
    
    # C. 最后渲染数字卡片
    # 这样它显示的就是刚刚算好的最新 target_price
    st.metric("目标租金 (Target)", f"${target_price:,.2f}")

# --- 3. 出租率仪表盘 ---
# --- 3. 出租率仪表盘 (蓝色调版) ---
st.write("---")
occ_rate = float(prop_data['Occupancy %']) * 100

fig_gauge = go.Figure(go.Indicator(
    mode = "gauge+number",
    value = occ_rate,
    number = {'suffix': "%", 'font': {'color': "#1f77b4"}}, # 数字也设为蓝色
    title = {'text': "Occupancy Rate", 'font': {'size': 20, 'color': "#1f77b4"}},
    domain = {'x': [0, 1], 'y': [0, 1]},
    gauge = {
        'axis': {'range': [0, 100], 'tickwidth': 1, 'tickcolor': "#1f77b4"},
        'bar': {'color': "#003f5c"}, # 进度指针用最深的颜色
        'bgcolor': "white",
        'borderwidth': 1,
        'bordercolor': "#e0e0e0",
        'steps': [
            {'range': [0, 70], 'color': "#f0f4f8"},   # 极浅蓝灰
            {'range': [70, 90], 'color': "#d1e3f0"},  # 浅蓝色
            {'range': [90, 100], 'color': "#a3c1da"}  # 中蓝色
        ],
        'threshold': {
            'line': {'color': "#ff4b4b", 'width': 3}, # 阈值线保留一点红色作为警示，或改为深蓝
            'thickness': 0.75,
            'value': 95}
    }
))

fig_gauge.update_layout(
    height=300, 
    margin=dict(l=30, r=30, t=50, b=20),
    paper_bgcolor = "rgba(0,0,0,0)", # 透明背景适应主题
)

st.plotly_chart(fig_gauge, use_container_width=True)



# --- 4. 敏感性分析矩阵 ---
st.write("---")
st.subheader("NOI 敏感性分析 (单一物业)")

# 局部滑轨控制矩阵范围
c1, c2 = st.columns(2)
with c1:
    r_range = st.slider("模拟租金范围", 800, 2000, (800, 2000), step=50, key="prop_rent")
with c2:
    v_range = st.slider("模拟空置数范围", 0, int(prop_data['Total Unit']), (0, 5), key="prop_vac")

# 生成矩阵 (传入只含该物业的 DataFrame)
single_prop_df = final_df[final_df['Property ID'] == prop_id]
rent_levels = np.arange(r_range[0], r_range[1] + 50, 100)
vac_levels = list(range(v_range[0], v_range[1] + 1))

noi_matrix = generate_dynamic_noi_matrix(single_prop_df, rent_levels, vac_levels)

# 展示矩阵
st.dataframe(
    noi_matrix.style.background_gradient(cmap='RdYlGn', axis=None).format("${:,.0f}"),
    use_container_width=True
)
