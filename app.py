import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import os
import re
import time
import base64
from datetime import datetime
import io

# --- HIDE STREAMLIT UI ---
st.set_page_config(page_title="Gift Selection App", page_icon="🎁", layout="wide")
hide_st_style = """
            <style>
            header {visibility: hidden !important;}
            [data-testid="stHeader"] {display: none !important;}
            [data-testid="stDecoration"] {display: none !important;}
            [data-testid="stToolbar"] {display: none !important;}
            footer {visibility: hidden !important;}
            [data-testid="stFooter"] {display: none !important;}
            .viewerBadge_container {display: none !important;}
            .viewerBadge_link {display: none !important;}
            [data-testid="stViewerBadge"] {display: none !important;}
            #viewerBadge {display: none !important;}
            </style>
            """
st.markdown(hide_st_style, unsafe_allow_html=True)

# --- 1. CONNECT & CACHE DATABASE (FIXES NEON DATA LIMIT) ---
DATABASE_URL = os.environ.get("DB_URL")

@st.cache_resource
def init_connection():
    if not DATABASE_URL:
        st.error("Database URL is missing! Please add DB_URL to your Streamlit Secrets.")
        st.stop()
        
    clean_url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://")
    
    return create_engine(
        clean_url, 
        pool_pre_ping=True, 
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
        connect_args={
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5
        }
    )

engine = init_connection()

# This tells Streamlit to only download the data once every 1 Hour!
@st.cache_data(ttl=3600)
def load_database_data():
    with engine.connect() as conn:
        cust = pd.read_sql("SELECT * FROM sales_data", conn)
        gfts = pd.read_sql("SELECT * FROM gift_slabs", conn)
        try:
            csts = pd.read_sql("SELECT * FROM slab_costs", conn)
        except Exception:
            csts = pd.DataFrame(columns=['SLAB', 'COST'])
            
        try:
            prim = pd.read_sql("SELECT * FROM primary_sales", conn)
        except Exception:
            prim = pd.DataFrame() 
            
    return cust, gfts, csts, prim

try:
    customers_raw, gifts_raw, costs_df, primary_raw = load_database_data()
    customers = customers_raw.copy()
    gifts = gifts_raw.copy()
    primary_df = primary_raw.copy() 
    
    # ==========================================
    # 🛑 ULTIMATE DATA CLEANUP & CSV FIX
    # ==========================================

    # 1. Fix PostgreSQL's automatic lowercase bug! 
    # This scans your tables and forces the headers back to exactly what Python expects.
    for col in gifts.columns:
        if 'item' in col.lower() and 'name' in col.lower():
            gifts.rename(columns={col: 'ITEM NAME '}, inplace=True)
        if 'slab' in col.lower():
            gifts.rename(columns={col: 'SLAB'}, inplace=True)
            
    for col in costs_df.columns:
        if 'slab' in col.lower():
            costs_df.rename(columns={col: 'SLAB'}, inplace=True)
        if 'cost' in col.lower():
            costs_df.rename(columns={col: 'COST'}, inplace=True)

    # 2. Fix the Float vs Integer Bug (Forces exact numeric matches)
    if 'SLAB' in gifts.columns:
        gifts['SLAB'] = pd.to_numeric(gifts['SLAB'], errors='coerce')
    if 'SLAB' in costs_df.columns:
        costs_df['SLAB'] = pd.to_numeric(costs_df['SLAB'], errors='coerce')
    if 'Total' in customers.columns:
        customers['Total'] = pd.to_numeric(customers['Total'], errors='coerce')
        
    # 3. Fix the "Unknown Gift" hidden space bug
    if 'ITEM NAME ' in gifts.columns:
        gifts['ITEM NAME '] = gifts['ITEM NAME '].astype(str).str.strip()

    # ==========================================
    
    # Safely drop any completely empty rows before building the dictionary
    costs_clean = costs_df.dropna(subset=['SLAB', 'COST'])
    slab_to_cost = {float(row['SLAB']): float(row['COST']) for _, row in costs_clean.iterrows()}
    
    # Fix the 1 Crore typo in the database
    gifts.loc[gifts['SLAB'] == 10000000, 'SLAB'] = 1000000
    
    # --- BULLETPROOF COLUMN CREATION ---
    columns_to_add = {        
         'selected_gift': "TEXT",
        'delivery_status': "TEXT DEFAULT 'Pending'",
        'delivery_photo': "TEXT",
        'delivery_lat': "TEXT",
        'delivery_lon': "TEXT",
        'delivery_address': "TEXT",
        'delivery_time': "TEXT",
        'is_blocked': "TEXT DEFAULT 'No'"
    }
    
    # Force add columns if they don't exist in the database yet
    for col, col_type in columns_to_add.items():
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE sales_data ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            pass # Ignore if there's a minor sync error, column already exists
            
    # Apply defaults to the dataframe memory
    for col, col_type in columns_to_add.items():
        if col not in customers.columns:
            if 'DEFAULT' in col_type:
                if 'Pending' in col_type:
                    customers[col] = "Pending"
                elif 'No' in col_type:
                    customers[col] = "No"
            else:
                customers[col] = ""
        else:
            if col == 'delivery_status':
                customers[col] = customers[col].fillna("Pending")
            elif col == 'is_blocked':
                customers[col] = customers[col].fillna("No")
            else:
                customers[col] = customers[col].fillna("")
        
except Exception as e:
    st.error(f"Database connection failed. Details: {e}")
    st.stop()

# --- 2. URL PARAMETER LOGIN SYSTEM ---
if 'logged_in' not in st.session_state:
    if "role" in st.query_params:
        st.session_state.logged_in = True
        st.session_state.role = st.query_params["role"]
        st.session_state.scope = st.query_params["scope"]
        if st.session_state.scope.isdigit():
            st.session_state.scope = int(st.session_state.scope)
        st.session_state.username = st.query_params.get("user", "User")
    else:
        st.session_state.logged_in = False
        st.session_state.username = None
        st.session_state.role = None
        st.session_state.scope = None

# --- 3. LOGIN UI ---
if not st.session_state.logged_in:
    st.title("🔐 Secure Login")
    st.markdown("Please log in to access your gift allocation portal.")
    
    with st.form("login_form"):
        username = st.text_input("Username (Admin / District / PCID)")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
        
        if submitted:
            if username.lower() == 'admin' and password == 'admin123':
                st.query_params["role"] = "admin"
                st.query_params["scope"] = "ALL"
                st.query_params["user"] = "Admin"
                st.session_state.logged_in = True
                st.session_state.role = "admin"
                st.session_state.scope = "ALL"
                st.session_state.username = "Admin"
                st.rerun()
                
            elif username.upper() in customers['ParentCompanyDistrict'].dropna().unique():
                if password == '1234':
                    st.query_params["role"] = "district"
                    st.query_params["scope"] = username.upper()
                    st.query_params["user"] = username.upper()
                    st.session_state.logged_in = True
                    st.session_state.role = "district"
                    st.session_state.scope = username.upper()
                    st.session_state.username = username.upper()
                    st.rerun()
                else:
                    st.error("Incorrect password for District.")
                    
            elif username.isdigit() and int(username) in customers['pcidd'].dropna().unique():
                if password == '1234':
                    st.query_params["role"] = "parent_company"
                    st.query_params["scope"] = str(username)
                    st.query_params["user"] = f"PCID - {username}"
                    st.session_state.logged_in = True
                    st.session_state.role = "parent_company"
                    st.session_state.scope = int(username)
                    st.session_state.username = f"PCID - {username}"
                    st.rerun()
                else:
                    st.error("Incorrect password for Parent Company.")
            else:
                st.error("User not found.")
    st.stop()
    
# --- 4. MAIN DASHBOARD ---

# --- SMART DISPLAY NAME (Fetches actual Parent Company Name) ---
if st.session_state.role == 'parent_company':
    try:
        # Look up the actual Parent Company Name from the database using their scope (PCID)
        pc_name = customers[customers['pcidd'] == st.session_state.scope]['ParentCompanyName'].iloc[0]
        display_name = f"{pc_name} (PCID: {st.session_state.scope})"
    except:
        display_name = st.session_state.username
else:
    display_name = st.session_state.username

# --- MOBILE-FRIENDLY TOP BAR & LOGOUT ---
st.title("🎁 Gift Allocation Dashboard")

head_col1, head_col2 = st.columns([3, 1])
with head_col1:
    st.markdown(f"### 👤 Welcome, {display_name}")
    st.markdown(f"**Role:** {st.session_state.role.title().replace('_', ' ')}")
with head_col2:
    st.write("") # Spacer to push the button down slightly
    if st.button("🚪 Log Out", use_container_width=True, type="primary"):
        st.query_params.clear()
        st.session_state.clear()
        st.rerun()
        
st.divider()

# --- DEFINE BASE_DF BASED ON USER ROLE ---
if st.session_state.role == 'admin':
    base_df = customers.copy()
elif st.session_state.role == 'district':
    base_df = customers[customers['ParentCompanyDistrict'] == st.session_state.scope].copy()
elif st.session_state.role == 'parent_company':
    base_df = customers[customers['pcidd'] == st.session_state.scope].copy()
else:
    base_df = pd.DataFrame() 
            
# --- DEFINE BASE_DF BASED ON USER ROLE ---
if st.session_state.role == 'admin':
    base_df = customers.copy()
elif st.session_state.role == 'district':
    base_df = customers[customers['ParentCompanyDistrict'] == st.session_state.scope].copy()
elif st.session_state.role == 'parent_company':
    base_df = customers[customers['pcidd'] == st.session_state.scope].copy()
else:
    base_df = pd.DataFrame() 
            
# --- DYNAMIC TABS BASED ON ROLE ---
if st.session_state.role == 'admin':
    tabs = st.tabs(["🎁 Allocate Gifts", "📊 Customer Wise Report", "📦 Projected Breakdown", "🛍️ Locked Gifts Breakdown", "🚚 Deliver Gifts", "🗺️ Admin Map & Proofs", "📈 Primary vs Secondary"])
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = tabs[0], tabs[1], tabs[2], tabs[3], tabs[4], tabs[5], tabs[6]
elif st.session_state.role == 'district':
    tabs = st.tabs(["🎁 Allocate Gifts", "📊 Customer Wise Report", "📦 Projected Breakdown", "🛍️ Locked Gifts Breakdown", "🚚 Deliver Gifts"])
    tab1, tab2, tab3, tab4, tab5 = tabs[0], tabs[1], tabs[2], tabs[3], tabs[4]
    tab6 = None
    tab7 = None
else:
    tabs = st.tabs(["🎁 Allocate Gifts", "📊 Customer Wise Report", "🛍️ Locked Gifts Breakdown", "🚚 Deliver Gifts"])
    tab1, tab2, tab4, tab5 = tabs[0], tabs[1], tabs[2], tabs[3]
    tab3 = None
    tab6 = None
    tab7 = None
# --------- TAB 1: ALLOCATE GIFTS ---------
with tab1:
    st.subheader("1️⃣ Select a Customer")
    selected_mobile = None
    
    if st.session_state.role == 'admin':
        districts = ["-- Select District --"] + sorted(base_df['ParentCompanyDistrict'].dropna().unique().tolist())
        sel_dist = st.selectbox("Select District:", districts)
        if sel_dist != "-- Select District --":
            parents = ["-- Select Parent Company --"] + sorted(base_df[base_df['ParentCompanyDistrict'] == sel_dist]['ParentCompanyName'].dropna().unique().tolist())
            sel_parent = st.selectbox("Select Parent Company:", parents)
            if sel_parent != "-- Select Parent Company --":
                companies_df = base_df[(base_df['ParentCompanyDistrict'] == sel_dist) & (base_df['ParentCompanyName'] == sel_parent)]
                comp_options = ["-- Select Company --"] + companies_df.apply(lambda row: f"{row['CompanyName']} (Mobile: {row['customermobile']})", axis=1).tolist()
                sel_comp = st.selectbox("Select Company:", comp_options)
                if sel_comp != "-- Select Company --":
                    mobile_match = re.search(r"Mobile: (\d+)", sel_comp)
                    selected_mobile = int(mobile_match.group(1))

    elif st.session_state.role == 'district':
        parents = ["-- Select Parent Company --"] + sorted(base_df['ParentCompanyName'].dropna().unique().tolist())
        sel_parent = st.selectbox("Select Parent Company:", parents)
        if sel_parent != "-- Select Parent Company --":
            companies_df = base_df[base_df['ParentCompanyName'] == sel_parent]
            comp_options = ["-- Select Company --"] + companies_df.apply(lambda row: f"{row['CompanyName']} (Mobile: {row['customermobile']})", axis=1).tolist()
            sel_comp = st.selectbox("Select Company:", comp_options)
            if sel_comp != "-- Select Company --":
                mobile_match = re.search(r"Mobile: (\d+)", sel_comp)
                selected_mobile = int(mobile_match.group(1))
                
    elif st.session_state.role == 'parent_company':
        comp_options = ["-- Select Company --"] + base_df.apply(lambda row: f"{row['CompanyName']} (Mobile: {row['customermobile']})", axis=1).tolist()
        sel_comp = st.selectbox("Select Company:", comp_options)
        if sel_comp != "-- Select Company --":
            mobile_match = re.search(r"Mobile: (\d+)", sel_comp)
            selected_mobile = int(mobile_match.group(1))

    if selected_mobile:
        customer_data = base_df[base_df['customermobile'] == selected_mobile].iloc[0]
        customer_points = customer_data['Total']
        current_allocation = customer_data['selected_gift']
        delivery_status = customer_data.get('delivery_status', 'Pending')
        is_blocked = customer_data.get('is_blocked', 'No')
        
        st.info(f"🪙 **Available Points:** {customer_points} | **Credit Limit:** {customer_data['CreditLimit']}")

        if st.session_state.role == 'admin':
            if is_blocked == 'Yes':
                st.error("🚫 **This customer is currently BLOCKED.** They are hidden from projected reports and cannot be allocated gifts.")
                if st.button("🔓 Unblock Customer", type="primary"):
                    with engine.begin() as conn:
                        conn.execute(text("UPDATE sales_data SET is_blocked = 'No' WHERE CAST(customermobile AS TEXT) = :mobile"), {"mobile": str(selected_mobile)})
                    load_database_data.clear()
                    st.rerun()
            else:
                if st.button("🚫 Block Customer"):
                    with engine.begin() as conn:
                        conn.execute(text("UPDATE sales_data SET is_blocked = 'Yes', selected_gift = '', delivery_status = 'Pending' WHERE CAST(customermobile AS TEXT) = :mobile"), {"mobile": str(selected_mobile)})
                    load_database_data.clear()
                    st.rerun()

        if is_blocked == 'Yes':
            st.warning("⚠️ Gift allocation is disabled while the customer is blocked. Unblock them above to restore access.")
        else:
            if current_allocation and str(current_allocation).strip() != "":
                st.success(f"🔒 **Gift Locked:** {current_allocation}")
                
                if delivery_status == 'Delivered':
                    st.success("✅ **STATUS: DELIVERED** - This gift has already been handed over to the customer.")
                else:
                    if st.session_state.role == 'admin':
                        if st.button("Revoke / Change Allocation (Admin Only)"):
                            with engine.begin() as conn:
                                query = text("UPDATE sales_data SET selected_gift = '' WHERE CAST(customermobile AS TEXT) = :mobile")
                                conn.execute(query, {"mobile": str(selected_mobile)})
                            
                            load_database_data.clear() 
                            st.rerun()
                    else:
                        st.info("This allocation is locked. Contact an admin if you need assistance.")
            else:
                st.subheader("2️⃣ Allocate Gifts")
                total_spent = 0
                selections = {}
                gifts_sorted = gifts.sort_values(by="SLAB", ascending=True)

                col1, col2 = st.columns([2, 1])
                with col1:
                    for index, row in gifts_sorted.iterrows():
                        gift_name = row['ITEM NAME ']
                        gift_points = int(row['SLAB'])
                        
                        if gift_points <= customer_points:
                            max_qty = int(customer_points // gift_points)
                            row_col1, row_col2 = st.columns([3, 1])
                            row_col1.markdown(f"**{gift_name}** \n*(Cost: {gift_points} pts)*")
                            qty = row_col2.number_input(f"Qty##{gift_name}", min_value=0, max_value=max_qty, value=0, label_visibility="collapsed")
                            if qty > 0:
                                selections[gift_name] = {'qty': qty, 'points': gift_points}
                                total_spent += (qty * gift_points)

                with col2:
                    st.markdown("### Cart Summary")
                    points_remaining = customer_points - total_spent
                    if total_spent > customer_points:
                        st.error(f"❌ Over limit by {total_spent - customer_points} pts!")
                    else:
                        st.metric(label="Total Points Spent", value=total_spent)
                        st.metric(label="Points Remaining", value=points_remaining)
                        if total_spent > 0 and st.button("Lock in Gift Selection", use_container_width=True):
                            chosen_items = [f"{g} (x{d['qty']})" for g, d in selections.items() if d['qty'] > 0]
                            final_gift_string = ", ".join(chosen_items)
                            with engine.begin() as conn:
                                query = text("UPDATE sales_data SET selected_gift = :gift, delivery_status = 'Pending' WHERE CAST(customermobile AS TEXT) = :mobile")
                                conn.execute(query, {"gift": final_gift_string, "mobile": str(selected_mobile)})
                            st.success(f"🎉 Successfully locked in: **{final_gift_string}**!")
                            st.balloons()
                            time.sleep(1.5)
                            
                            load_database_data.clear() 
                            st.rerun()

# --------- TAB 2: CUSTOMER WISE REPORT ---------
with tab2:
    st.subheader("📊 Customer Wise Report")
    report_df = base_df.copy()
    display_cols = ['ParentCompanyDistrict', 'ParentCompanyName', 'CompanyName', 'customermobile', 'Total', 'selected_gift', 'delivery_status', 'delivery_time', 'is_blocked']
    
    # --- NEW: INSTANT BLOCKED CUSTOMER FILTER ---
    show_blocked = st.checkbox("🚫 Show Blocked Customers Only", help="Check this box to filter the table and only see blocked accounts.")
    if show_blocked:
        report_df = report_df[report_df.get('is_blocked', 'No') == 'Yes']
    
    if st.session_state.role == 'admin':
        col1, col2 = st.columns(2)
        with col1:
            dist_filter = st.selectbox("Filter by District (Optional):", ["All"] + sorted(report_df['ParentCompanyDistrict'].dropna().unique().tolist()))
            if dist_filter != "All":
                report_df = report_df[report_df['ParentCompanyDistrict'] == dist_filter]
        with col2:
            parent_filter = st.selectbox("Filter by Parent Company (Optional):", ["All"] + sorted(report_df['ParentCompanyName'].dropna().unique().tolist()))
            if parent_filter != "All":
                report_df = report_df[report_df['ParentCompanyName'] == parent_filter]
        
        if show_blocked and report_df.empty:
            st.success("🎉 Great news! There are zero blocked customers in this selection.")
        else:
            st.dataframe(report_df[display_cols], use_container_width=True)

    elif st.session_state.role == 'district':
        parent_filter = st.selectbox("Select Parent Company:", ["All Parent Companies"] + sorted(report_df['ParentCompanyName'].dropna().unique().tolist()))
        if parent_filter != "All Parent Companies":
            report_df = report_df[report_df['ParentCompanyName'] == parent_filter]
            
        if show_blocked and report_df.empty:
            st.success("🎉 Great news! There are zero blocked customers in this selection.")
        else:
            st.dataframe(report_df[display_cols], use_container_width=True)

    elif st.session_state.role == 'parent_company':
        if show_blocked and report_df.empty:
            st.success("🎉 Great news! You have zero blocked customers.")
        else:
            st.dataframe(report_df[display_cols], use_container_width=True)

    if not report_df.empty:
        csv = report_df[display_cols].to_csv(index=False).encode('utf-8')
        
        # Change the file name dynamically so you know it is a blocked list
        if show_blocked:
            file_export_name = f"BLOCKED_customer_report_{st.session_state.username}.csv"
        else:
            file_export_name = f"customer_report_{st.session_state.username}.csv"
            
        st.download_button(label="Download Report as CSV", data=csv, file_name=file_export_name, mime="text/csv")# --------- TAB 3: SLAB WISE REPORT (Projected) ---------
if tab3 is not None:
    with tab3:
        st.subheader("📦 Projected Slab Breakdown")
        
        # --- SAFE FILTER OUT BLOCKED CUSTOMERS ---
        if 'is_blocked' in base_df.columns:
            report_df_slab = base_df[base_df['is_blocked'] != 'Yes'].copy()
        else:
            report_df_slab = base_df.copy()
        
        if st.session_state.role == 'admin':
            dist_filter_slab = st.selectbox("Filter by District:", ["All Districts"] + sorted(report_df_slab['ParentCompanyDistrict'].dropna().unique().tolist()), key="slab_dist_1")
            if dist_filter_slab != "All Districts":
                report_df_slab = report_df_slab[report_df_slab['ParentCompanyDistrict'] == dist_filter_slab]
        
        slab_to_gift = {}
        for _, g_row in gifts.iterrows():
            try:
                slab_to_gift[float(g_row['SLAB'])] = str(g_row['ITEM NAME ']).strip()
            except:
                pass

        unique_slabs = sorted([float(x) for x in gifts['SLAB'].dropna().unique()], reverse=True)
        total_slab_counts = {slab: 0 for slab in unique_slabs}
        slab_customer_details = {slab: [] for slab in unique_slabs}
        
        for _, row in report_df_slab.iterrows():
            if pd.isna(row['Total']):
                continue
            
            remaining_pts = float(row['Total'])
            for slab in unique_slabs:
                if remaining_pts >= slab:
                    qty = int(remaining_pts // slab)
                    total_slab_counts[slab] += qty
                    
                    slab_customer_details[slab].append({
                        "District": row['ParentCompanyDistrict'],
                        "Parent Company": row['ParentCompanyName'],
                        "Company Name": row['CompanyName'],
                        "Mobile": row['customermobile'],
                        "Slab Amount": int(slab),
                        "Gift Name": slab_to_gift.get(slab, "Unknown Gift"),
                        "Quantity": qty,
                        "Total Points": row['Total']
                    })
                    remaining_pts = round(remaining_pts % slab, 2)
                    
        active_slabs = {k: v for k, v in total_slab_counts.items() if v > 0}
        
        if not active_slabs:
            st.info("No customers meet the minimum points required for any gift slab.")
        else:
            st.write("### 📈 Projected Summary")
            summary_data = []
            grand_total_gifts = sum(active_slabs.values())
            grand_total_spend = sum(count * slab_to_cost.get(slab, 0) for slab, count in active_slabs.items())

            for slab, count in active_slabs.items():
                g_name = slab_to_gift.get(slab, "Unknown Gift")
                row_data = {"Slab": str(int(slab)), "Gift Name": g_name, "Quantity": count}
                if st.session_state.role == 'admin':
                    unit_cost = slab_to_cost.get(slab, 0)
                    total_spend = count * unit_cost
                    total_slab_value = count * float(slab)
                    pct_of_grand = (total_spend / grand_total_spend * 100) if grand_total_spend > 0 else 0
                    reward_pct = (total_spend / total_slab_value * 100) if total_slab_value > 0 else 0
                    
                    row_data["Total Slab Value"] = total_slab_value
                    row_data["Unit Cost (₹)"] = unit_cost
                    row_data["Total Spend (₹)"] = total_spend
                    row_data["Reward %"] = f"{reward_pct:.2f}%"
                    row_data["% of Grand Total"] = f"{pct_of_grand:.2f}%"
                summary_data.append(row_data)

            sum_df = pd.DataFrame(summary_data)
            
            if st.session_state.role == 'admin' and not sum_df.empty:
                grand_slab_value = sum_df["Total Slab Value"].sum()
                grand_reward_pct = (grand_total_spend / grand_slab_value * 100) if grand_slab_value > 0 else 0
                
                grand_row = {
                    "Slab": "GRAND TOTAL",
                    "Gift Name": "-",
                    "Quantity": grand_total_gifts,
                    "Total Slab Value": grand_slab_value,
                    "Unit Cost (₹)": "-",
                    "Total Spend (₹)": grand_total_spend,
                    "Reward %": f"{grand_reward_pct:.2f}%",
                    "% of Grand Total": "100.00%"
                }
                
                sum_df["Total Slab Value"] = sum_df["Total Slab Value"].apply(lambda x: f"{x:,.0f}")
                sum_df["Unit Cost (₹)"] = sum_df["Unit Cost (₹)"].apply(lambda x: f"{x:,.2f}")
                sum_df["Total Spend (₹)"] = sum_df["Total Spend (₹)"].apply(lambda x: f"{x:,.2f}")
                
                grand_row["Total Slab Value"] = f"{grand_row['Total Slab Value']:,.0f}"
                grand_row["Total Spend (₹)"] = f"{grand_row['Total Spend (₹)']:,.2f}"
                
                sum_df = pd.concat([sum_df, pd.DataFrame([grand_row])], ignore_index=True)

            st.dataframe(sum_df, use_container_width=True)
            
            if st.session_state.role == 'admin':
                st.markdown(f"#### **Grand Total Gifts: {grand_total_gifts} | Grand Total Spend: ₹{grand_total_spend:,.2f}**")
            else:
                st.markdown(f"#### **Grand Total Gifts: {grand_total_gifts}**")
                
            st.divider()
            st.write("### 🔍 View Detailed Customer List")
            slab_options = ["-- Select a Option --", "-- All Active Slabs --"] + [f"{int(s)} - {slab_to_gift.get(s, 'Unknown Gift')}" for s in active_slabs.keys()]
            selected_slab_view = st.selectbox("Select a Slab:", slab_options, key="slab_dropdown_1")
            
            if selected_slab_view != "-- Select a Option --":
                df_details = pd.DataFrame()
                if selected_slab_view == "-- All Active Slabs --":
                    all_details = []
                    for s_data in slab_customer_details.values():
                        all_details.extend(s_data)
                    df_details = pd.DataFrame(all_details)
                    file_name_out = "all_projected_slabs_customers.csv"
                else:
                    slab_val = float(selected_slab_view.split(" - ")[0])
                    df_details = pd.DataFrame(slab_customer_details[slab_val])
                    file_name_out = f"slab_{int(slab_val)}_customers.csv"

                if not df_details.empty:
                    search_query = st.text_input("🔍 Search Name or District:", key="search_tab3")
                    if search_query:
                        df_details = df_details[df_details.astype(str).apply(lambda x: x.str.contains(search_query, case=False)).any(axis=1)]
                    
                    if st.session_state.role == 'admin':
                        df_details['Total Slab Value'] = df_details['Quantity'] * df_details['Slab Amount']
                        df_details['Unit Cost (₹)'] = df_details['Slab Amount'].map(slab_to_cost)
                        df_details['Total Spend (₹)'] = df_details['Quantity'] * df_details['Unit Cost (₹)']
                        df_details['Reward %'] = df_details.apply(
                            lambda r: f"{(r['Total Spend (₹)'] / r['Total Slab Value'] * 100):.2f}%" if r['Total Slab Value'] > 0 else "0.00%", axis=1
                        )
                    st.dataframe(df_details, use_container_width=True)
                    st.download_button(label="Download CSV", data=df_details.to_csv(index=False).encode('utf-8'), file_name=file_name_out, mime="text/csv")
                    
                    if st.session_state.role == 'admin':
                        st.write("")
                        with st.expander("🚫 Quick Block / Unblock Customer Status", expanded=False):
                            st.write("Search for any customer (Active or Blocked) in this district to manage their status.")
                            
                            manage_df = base_df.copy()
                            if dist_filter_slab != "All Districts":
                                manage_df = manage_df[manage_df['ParentCompanyDistrict'] == dist_filter_slab]
                                
                            block_options = ["-- Search Customer --"] + manage_df.apply(
                                lambda row: f"{row['CompanyName']} (Mobile: {row['customermobile']}) - Status: {'BLOCKED 🚫' if row.get('is_blocked', 'No') == 'Yes' else 'ACTIVE ✅'}", axis=1
                            ).tolist()
                            
                            sel_manage = st.selectbox("Select Customer:", block_options, key="manage_block_tab3")
                            
                            if sel_manage != "-- Search Customer --":
                                mobile_match = re.search(r"Mobile: (\d+)", sel_manage)
                                if mobile_match:
                                    target_mobile = int(mobile_match.group(1))
                                    target_data = manage_df[manage_df['customermobile'] == target_mobile].iloc[0]
                                    is_currently_blocked = target_data.get('is_blocked', 'No')
                                    
                                    col_b1, col_b2 = st.columns([3, 1])
                                    with col_b1:
                                        if is_currently_blocked == 'Yes':
                                            st.error(f"**{target_data['CompanyName']}** is currently BLOCKED.")
                                        else:
                                            st.success(f"**{target_data['CompanyName']}** is currently ACTIVE.")
                                            
                                    with col_b2:
                                        if is_currently_blocked == 'Yes':
                                            if st.button("🔓 Unblock", use_container_width=True, type="primary"):
                                                with engine.begin() as conn:
                                                    conn.execute(text("UPDATE sales_data SET is_blocked = 'No' WHERE CAST(customermobile AS TEXT) = :mobile"), {"mobile": str(target_mobile)})
                                                load_database_data.clear()
                                                st.rerun()
                                        else:
                                            if st.button("🚫 Block", use_container_width=True):
                                                with engine.begin() as conn:
                                                    conn.execute(text("UPDATE sales_data SET is_blocked = 'Yes', selected_gift = '', delivery_status = 'Pending' WHERE CAST(customermobile AS TEXT) = :mobile"), {"mobile": str(target_mobile)})
                                                load_database_data.clear()
                                                st.rerun()

                    st.divider()
                    st.write(f"### 🏢 Parent Company Breakdown for: {selected_slab_view}")
                    st.write("This report analyzes how the awarded gifts compare against the Parent Company's total created customers, Primary Sales, and Secondary Sales.")
                    
                    if primary_df is None or primary_df.empty:
                        st.warning("⚠️ Primary sales data not found. Please upload your Primary Data in Tab 7 to unlock this report.")
                    else:
                        pc_map1, pc_map2 = st.columns(2)
                        with pc_map1:
                            p_dist_col = st.selectbox("Primary DB: Distributor Column", primary_df.columns.tolist(), key="addon_dist_col")
                        with pc_map2:
                            num_cols_p = primary_df.select_dtypes(include=['number']).columns.tolist()
                            if not num_cols_p: num_cols_p = primary_df.columns.tolist()
                            p_val_col = st.selectbox("Primary DB: Sales Value Column", num_cols_p, key="addon_val_col")
                            
                        if st.button("Generate Parent Breakdown", type="secondary"):
                            with st.spinner("Calculating Breakdown..."):
                                df_temp = df_details.copy()
                                df_temp['Calculated_Slab_Value'] = df_temp['Quantity'] * df_temp['Slab Amount']
                                
                                parent_summary = df_temp.groupby(['District', 'Parent Company']).agg(
                                    Slab_Count=('Quantity', 'sum'),
                                    Total_Slab_Value=('Calculated_Slab_Value', 'sum')
                                ).reset_index()
                                
                                parent_summary['Parent Company'] = parent_summary['Parent Company'].astype(str).str.upper().str.strip()
                                
                                sec_sales_addon = base_df.groupby('ParentCompanyName').agg(
                                    Total_Secondary_Sales=('Total', 'sum'),
                                    Total_Created_Companies=('customermobile', 'nunique')
                                ).reset_index()
                                sec_sales_addon['ParentCompanyName'] = sec_sales_addon['ParentCompanyName'].astype(str).str.upper().str.strip()
                                sec_sales_addon.rename(columns={
                                    'ParentCompanyName': 'Parent Company', 
                                    'Total_Secondary_Sales': 'Total Secondary Sales',
                                    'Total_Created_Companies': 'Total Created Companies'
                                }, inplace=True)
                                
                                prim_sales_addon = primary_df.groupby(p_dist_col)[p_val_col].sum().reset_index()
                                prim_sales_addon[p_dist_col] = prim_sales_addon[p_dist_col].astype(str).str.upper().str.strip()
                                prim_sales_addon.rename(columns={p_dist_col: 'Parent Company', p_val_col: 'Total Primary Sales'}, inplace=True)
                                
                                merged_addon = pd.merge(parent_summary, sec_sales_addon, on='Parent Company', how='left').fillna(0)
                                merged_addon = pd.merge(merged_addon, prim_sales_addon, on='Parent Company', how='left').fillna(0)
                                
                                total_slab_gifts_for_view = parent_summary['Slab_Count'].sum()
                                
                                merged_addon['% of Total Slab Count'] = (merged_addon['Slab_Count'] / total_slab_gifts_for_view * 100)
                                merged_addon['% vs Primary Sales'] = (merged_addon['Total_Slab_Value'] / merged_addon['Total Primary Sales'] * 100)
                                merged_addon['% vs Secondary Sales'] = (merged_addon['Total_Slab_Value'] / merged_addon['Total Secondary Sales'] * 100)
                                merged_addon['% Companies Rewarded'] = (merged_addon['Slab_Count'] / merged_addon['Total Created Companies'] * 100)
                                
                                for col in ['% vs Primary Sales', '% vs Secondary Sales', '% of Total Slab Count', '% Companies Rewarded']:
                                    merged_addon[col] = merged_addon[col].replace([float('inf'), -float('inf')], 0).fillna(0)
                                
                                merged_addon.rename(columns={'Slab_Count': 'Gift Count'}, inplace=True)
                                
                                display_cols_list = [
                                    'District', 'Parent Company', 'Total Created Companies', 'Gift Count', 
                                    '% Companies Rewarded', '% of Total Slab Count', 
                                    'Total Primary Sales', 'Total Secondary Sales', 
                                    '% vs Primary Sales', '% vs Secondary Sales'
                                ]
                                
                                display_addon = merged_addon[display_cols_list].copy()
                                
                                display_addon['Total Primary Sales'] = display_addon['Total Primary Sales'].apply(lambda x: f"₹ {x:,.2f}")
                                display_addon['Total Secondary Sales'] = display_addon['Total Secondary Sales'].apply(lambda x: f"₹ {x:,.2f}")
                                display_addon['% of Total Slab Count'] = display_addon['% of Total Slab Count'].apply(lambda x: f"{x:,.2f}%")
                                display_addon['% vs Primary Sales'] = display_addon['% vs Primary Sales'].apply(lambda x: f"{x:,.2f}%")
                                display_addon['% vs Secondary Sales'] = display_addon['% vs Secondary Sales'].apply(lambda x: f"{x:,.2f}%")
                                display_addon['% Companies Rewarded'] = display_addon['% Companies Rewarded'].apply(lambda x: f"{x:,.2f}%")
                                
                                st.dataframe(display_addon, use_container_width=True)
                                
                                numeric_csv = merged_addon[display_cols_list].to_csv(index=False).encode('utf-8')
                                st.download_button(
                                    label="📥 Download Add-On Report (Numeric Format for Excel)", 
                                    data=numeric_csv, 
                                    file_name=f"Parent_Slab_Breakdown_{str(selected_slab_view).split(' - ')[0]}.csv", 
                                    mime="text/csv",
                                    type="primary"
                                )

# --------- TAB 4: LOCKED GIFTS BREAKDOWN ---------
with tab4:
    st.subheader("🛍️ Locked Gifts Breakdown")
    report_df_locked = base_df.copy()
    
    if st.session_state.role == 'admin':
        dist_filter_locked = st.selectbox("Filter by District:", ["All Districts"] + sorted(report_df_locked['ParentCompanyDistrict'].dropna().unique().tolist()), key="locked_dist")
        if dist_filter_locked != "All Districts":
            report_df_locked = report_df_locked[report_df_locked['ParentCompanyDistrict'] == dist_filter_locked]
    elif st.session_state.role == 'district':
        parent_filter_locked = st.selectbox("Filter by Parent Company:", ["All Parent Companies"] + sorted(report_df_locked['ParentCompanyName'].dropna().unique().tolist()), key="locked_parent")
        if parent_filter_locked != "All Parent Companies":
            report_df_locked = report_df_locked[report_df_locked['ParentCompanyName'] == parent_filter_locked]

    gift_to_cost = {}
    gift_to_slab = {}
    for _, g_row in gifts.iterrows():
        try:
            slab_val = float(g_row['SLAB'])
            g_name = str(g_row['ITEM NAME ']).strip()
            gift_to_cost[g_name] = slab_to_cost.get(slab_val, 0)
            gift_to_slab[g_name] = slab_val
        except:
            pass

    locked_gift_counts = {}
    locked_customer_details = {}

    for _, row in report_df_locked.iterrows():
        sel_gift = str(row['selected_gift']).strip()
        if not sel_gift:
            continue
            
        items = [i.strip() for i in sel_gift.split(',')]
        for item in items:
            match = re.match(r"(.*) \(x(\d+)\)", item)
            if match:
                g_name = match.group(1).strip()
                g_qty = int(match.group(2))
                if g_qty > 0:
                    locked_gift_counts[g_name] = locked_gift_counts.get(g_name, 0) + g_qty
                    if g_name not in locked_customer_details:
                        locked_customer_details[g_name] = []
                    locked_customer_details[g_name].append({
                        "District": row['ParentCompanyDistrict'],
                        "Parent Company": row['ParentCompanyName'],
                        "Company Name": row['CompanyName'],
                        "Mobile": row['customermobile'],
                        "Slab Amount": int(gift_to_slab.get(g_name, 0)),
                        "Gift Name": g_name,
                        "Quantity Locked": g_qty,
                        "Delivery Status": row.get('delivery_status', 'Pending')
                    })

    if not locked_gift_counts:
        st.info("No gifts have been officially locked in yet for the selected scope.")
    else:
        st.write("### 📈 Officially Locked Summary")
        locked_summary_data = []
        grand_total_locked_gifts = sum(locked_gift_counts.values())
        grand_total_locked_spend = sum(count * gift_to_cost.get(g_name, 0) for g_name, count in locked_gift_counts.items())

        for g_name, count in locked_gift_counts.items():
            slab_val = gift_to_slab.get(g_name, 0)
            row_data = {"Slab": int(slab_val), "Gift Name": g_name, "Quantity": count}
            if st.session_state.role == 'admin':
                unit_cost = gift_to_cost.get(g_name, 0)
                total_spend = count * unit_cost
                total_slab_value = count * float(slab_val)
                pct_of_grand = (total_spend / grand_total_locked_spend * 100) if grand_total_locked_spend > 0 else 0
                reward_pct = (total_spend / total_slab_value * 100) if total_slab_value > 0 else 0
                row_data["Total Slab Value"] = f"{total_slab_value:,.0f}"
                row_data["Unit Cost (₹)"] = f"{unit_cost:,.2f}"
                row_data["Total Spend (₹)"] = f"{total_spend:,.2f}"
                row_data["Reward %"] = f"{reward_pct:.2f}%"
                row_data["% of Grand Total"] = f"{pct_of_grand:.2f}%"
            locked_summary_data.append(row_data)

        st.dataframe(pd.DataFrame(locked_summary_data), use_container_width=True)

        if st.session_state.role == 'admin':
            st.markdown(f"#### **Grand Total Locked Gifts: {grand_total_locked_gifts} | Grand Total Locked Spend: ₹{grand_total_locked_spend:,.2f}**")
        else:
            st.markdown(f"#### **Grand Total Locked Gifts: {grand_total_locked_gifts}**")
            
        st.divider()
        st.write("### 🔍 View Detailed Customer List")
        gift_options = ["-- Select a Option --", "-- All Locked Gifts --"] + sorted(locked_gift_counts.keys())
        selected_gift_view = st.selectbox("Select a Gift:", gift_options, key="gift_dropdown_1")
        
        if selected_gift_view != "-- Select a Option --":
            df_details_locked = pd.DataFrame()
            if selected_gift_view == "-- All Locked Gifts --":
                all_locked_data = []
                for g_data in locked_customer_details.values():
                    all_locked_data.extend(g_data)
                df_details_locked = pd.DataFrame(all_locked_data)
                file_name_out_locked = "all_locked_gifts_customers.csv"
            else:
                details_data_locked = locked_customer_details[selected_gift_view]
                if details_data_locked:
                    df_details_locked = pd.DataFrame(details_data_locked)
                    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', selected_gift_view)
                    file_name_out_locked = f"locked_{safe_name}_customers.csv"
            
            if not df_details_locked.empty:
                search_query_4 = st.text_input("🔍 Search Name or District:", key="search_tab4")
                if search_query_4:
                    df_details_locked = df_details_locked[df_details_locked.astype(str).apply(lambda x: x.str.contains(search_query_4, case=False)).any(axis=1)]
                
                if st.session_state.role == 'admin':
                    df_details_locked['Total Slab Value'] = df_details_locked['Quantity Locked'] * df_details_locked['Slab Amount']
                    df_details_locked['Unit Cost (₹)'] = df_details_locked['Gift Name'].map(gift_to_cost)
                    df_details_locked['Total Spend (₹)'] = df_details_locked['Quantity Locked'] * df_details_locked['Unit Cost (₹)']
                    df_details_locked['Reward %'] = df_details_locked.apply(
                        lambda r: f"{(r['Total Spend (₹)'] / r['Total Slab Value'] * 100):.2f}%" if r['Total Slab Value'] > 0 else "0.00%", axis=1
                    )
                st.dataframe(df_details_locked, use_container_width=True)
                st.download_button(label="Download CSV", data=df_details_locked.to_csv(index=False).encode('utf-8'), file_name=file_name_out_locked, mime="text/csv")
# ==========================================
        # 🧮 ADVANCED MATRIX REPORT (DISTRICT & PARENT COMPANY ONLY)
        # ==========================================
        st.divider()
        st.write("### 🧮 Parent Company Slab Matrix Report")
        st.write("This report shows the total locked gift articles assigned to each Parent Company, complete with District Totals and Grand Totals.")
        
        # 1. Build the matrix data (Strictly District and Parent Company)
        matrix_data = []
        for _, row_m in report_df_locked.iterrows():
            sel_gift = str(row_m['selected_gift']).strip()
            if not sel_gift:
                continue
                
            items = [i.strip() for i in sel_gift.split(',')]
            for item in items:
                match = re.match(r"(.*) \(x(\d+)\)", item)
                if match:
                    g_name = match.group(1).strip()
                    g_qty = int(match.group(2))
                    if g_qty > 0:
                        slab_val = int(gift_to_slab.get(g_name, 0))
                        
                        # Create the Excel wrapped-text heading (e.g., "Slab 10,000 \n (Silver Coin)")
                        header_str = f"Slab {slab_val:,}\n({g_name})"
                        
                        matrix_data.append({
                            "District": row_m['ParentCompanyDistrict'],
                            "Parent Company": row_m['ParentCompanyName'],
                            "Slab Header": header_str,
                            "Slab Amount": slab_val,
                            "Quantity": g_qty
                        })
                        
        df_matrix = pd.DataFrame(matrix_data)
        
        if df_matrix.empty:
            st.info("No gifts have been locked yet, so the matrix is empty.")
        else:
            # 2. Build the Pivot Table (Grouped ONLY by District and Parent Company)
            pivot_df = pd.pivot_table(
                df_matrix, 
                values='Quantity', 
                index=['District', 'Parent Company'], 
                columns='Slab Header', 
                aggfunc='sum', 
                fill_value=0
            ).reset_index()
            
            # 3. Sort the slab columns numerically lowest to highest
            header_to_amount = df_matrix[['Slab Header', 'Slab Amount']].drop_duplicates().set_index('Slab Header')['Slab Amount'].to_dict()
            slab_cols = [c for c in pivot_df.columns if c not in ['District', 'Parent Company']]
            slab_cols = sorted(slab_cols, key=lambda x: header_to_amount.get(x, 0))
            
            # 4. Calculate District Totals
            dist_totals = pivot_df.groupby('District')[slab_cols].sum().reset_index()
            dist_totals['Parent Company'] = '👉 TOTAL FOR ' + dist_totals['District']
            
            # 5. Calculate the Grand Total
            grand_total = pivot_df[slab_cols].sum().to_frame().T
            grand_total['District'] = '🌟 GRAND TOTAL'
            grand_total['Parent Company'] = ''
            
            # 6. Combine everything safely using a Sort Key
            pivot_df['Sort_Key'] = 1
            dist_totals['Sort_Key'] = 2
            grand_total['Sort_Key'] = 3
            
            combined_df = pd.concat([pivot_df, dist_totals, grand_total], ignore_index=True)
            combined_df = combined_df.sort_values(by=['District', 'Sort_Key', 'Parent Company'])
            combined_df = combined_df.drop(columns=['Sort_Key'])
            
            # 7. Final display order
            final_cols = ['District', 'Parent Company'] + slab_cols
            combined_df = combined_df[final_cols]
            
            # Show on screen
            st.dataframe(combined_df, use_container_width=True)
            
            # CSV Export
            csv_matrix = combined_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Advanced Matrix Report (CSV)", 
                data=csv_matrix, 
                file_name="Parent_Company_Slab_Matrix.csv", 
                mime="text/csv",
                type="primary",
                key="matrix_dl_btn_advanced"
            )
        # ==========================================
# ==========================================
        # 🎁 SPECIAL AGARBATHI CALCULATION REPORT (SO MAPPED)
        # ==========================================
        st.divider()
        st.write("### 🎁 Special Agarbathi Calculation Report (SO Wise)")
        st.write("Calculates Darbar 200g/Baba Harathi packets, mapped by SO, sorted, with highlighted bold totals.")
        
        # Hardcoded SO Mapping based on District Names
        def get_so_name(district_name):
            dist_str = str(district_name).upper()
            if "GUNTUR" in dist_str or "KRISHNA" in dist_str or "NELLORE" in dist_str or "PRAKASAM" in dist_str:
                return "karunakar"
            elif "EAST GOD" in dist_str or "SRIKAKULAM" in dist_str or "VISAKHAPATNAM" in dist_str or "VIZIANAGARAM" in dist_str or "WEST GOD" in dist_str:
                return "kiran"
            elif "ODISHA" in dist_str:
                return "shyam"
            elif "RANGAREDDY" in dist_str:
                return "swamyso"
            elif "KARIMNAGAR" in dist_str or "KHAMMAM" in dist_str or "NALGONDA" in dist_str or "WARANGAL" in dist_str:
                return "UPENDRA"
            elif "ANANTHAPUR" in dist_str or "CHITTOOR" in dist_str or "KADAPA" in dist_str or "KURNOOL" in dist_str:
                return "venky"
            elif "ADILABAD" in dist_str or "MAHABUB" in dist_str or "MEDAK" in dist_str or "NIZAMABAD" in dist_str:
                return "VITTAL DEV"
            return "Unknown SO"

        special_data = []
        agg_dict = {}
        
        # 1. Look through all locked gifts and count only the 3L, 5L, and 10L slabs
        for _, row_m in report_df_locked.iterrows():
            sel_gift = str(row_m['selected_gift']).strip()
            if not sel_gift:
                continue
                
            dist = row_m['ParentCompanyDistrict']
            parent_exact = row_m['ParentCompanyName']
            so_name = get_so_name(dist)  # Apply the hardcoded mapping
            
            items = [i.strip() for i in sel_gift.split(',')]
            for item in items:
                match = re.match(r"(.*) \(x(\d+)\)", item)
                if match:
                    g_name = match.group(1).strip()
                    g_qty = int(match.group(2))
                    
                    if g_qty > 0:
                        slab_val = int(gift_to_slab.get(g_name, 0))
                        
                        # Only care about 3 Lakhs, 5 Lakhs, and 10 Lakhs
                        if slab_val in [300000, 500000, 1000000]:
                            key = (so_name, dist, parent_exact)
                            if key not in agg_dict:
                                agg_dict[key] = {300000: 0, 500000: 0, 1000000: 0}
                            agg_dict[key][slab_val] += g_qty

        # 2. Process the Custom Math and Hardcoded Rules
        for (so_name, dist, parent), slabs in agg_dict.items():
            qty_3l = slabs[300000]
            qty_5l = slabs[500000]
            qty_10l = slabs[1000000]
            
            # Adjourn Value Calculation
            total_val = (qty_3l * 11000) + (qty_5l * 22000) + (qty_10l * 55000)
            
            if total_val > 0:
                parent_upper = str(parent).upper()
                
                # Hardcoded logic specifically for Sathish Agencies, Miryalguda
                if "SATHISH AGENCIES" in parent_upper and "MIRYALGUDA" in parent_upper:
                    product_name = "Baba Harathi 100g"
                    packets = int(total_val / 36.13)
                else:
                    product_name = "Darbar 200g"
                    packets = int(total_val / 94.59)
                    
                special_data.append({
                    "SO Name": so_name,
                    "District": dist,
                    "Parent Company": parent,
                    "3 Lakhs Slab": qty_3l,
                    "5 Lakhs Slab": qty_5l,
                    "10 Lakhs Slab": qty_10l,
                    "Total Adjourn Value (₹)": total_val,
                    "Product Allocated": product_name,
                    "Total Packets": packets
                })
                
        df_special = pd.DataFrame(special_data)
        
        if df_special.empty:
            st.info("No 3L, 5L, or 10L slabs have been locked yet for this selection.")
        else:
            # 3. Calculate District and Grand Totals safely
            dist_cols_to_sum = ["3 Lakhs Slab", "5 Lakhs Slab", "10 Lakhs Slab", "Total Adjourn Value (₹)", "Total Packets"]
            
            # Group by SO Name AND District so the totals stay grouped properly
            dist_totals = df_special.groupby(['SO Name', 'District'])[dist_cols_to_sum].sum().reset_index()
            dist_totals['Parent Company'] = '👉 TOTAL FOR ' + dist_totals['District'].astype(str).str.upper()
            dist_totals['Product Allocated'] = ''
            dist_totals['Sort_Key'] = 2
            
            grand_total = df_special[dist_cols_to_sum].sum().to_frame().T
            grand_total['SO Name'] = '🌟'
            grand_total['District'] = '🌟 GRAND TOTAL'
            grand_total['Parent Company'] = ''
            grand_total['Product Allocated'] = ''
            grand_total['Sort_Key'] = 3
            
            df_special['Sort_Key'] = 1
            
            # Combine everything and Sort SO Wise
            combined_special = pd.concat([df_special, dist_totals, grand_total], ignore_index=True)
            combined_special = combined_special.sort_values(by=['SO Name', 'District', 'Sort_Key', 'Parent Company'])
            
            # Format the currency column so it looks clean with commas
            display_special = combined_special.copy()
            display_special["Total Adjourn Value (₹)"] = display_special["Total Adjourn Value (₹)"].apply(lambda x: f"₹ {x:,.0f}" if pd.notnull(x) else "")
            
            # Final columns list
            final_spec_cols = ["SO Name", "District", "Parent Company", "3 Lakhs Slab", "5 Lakhs Slab", "10 Lakhs Slab", "Total Adjourn Value (₹)", "Product Allocated", "Total Packets"]
            display_special = display_special[final_spec_cols]
            
            # 4. HIGHLIGHT THE TOTALS BOLD IN STREAMLIT
            def highlight_totals(row):
                if "👉 TOTAL FOR" in str(row['Parent Company']) or "🌟 GRAND TOTAL" in str(row['District']):
                    return ['background-color: #e6f2ff; font-weight: bold; color: black'] * len(row)
                return [''] * len(row)
                
            styled_df = display_special.style.apply(highlight_totals, axis=1)
            
            # Show on Screen
            st.dataframe(styled_df, use_container_width=True)
            
            # Add Download Button (Using the raw combined dataframe so Excel numbers don't break)
            csv_special = combined_special[final_spec_cols].to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Special Agarbathi Report (CSV)", 
                data=csv_special, 
                file_name="Special_Agarbathi_Calculation_SOWise.csv", 
                mime="text/csv",
                type="primary",
                key="special_agarbathi_dl_btn"
            )
        # ==========================================
# --------- TAB 5: DELIVER GIFTS ---------
if tab5 is not None:
    with tab5:
        st.subheader("🚚 Deliver Gifts")
        
        # --- LAZY LOADING IMPORTS (Only loads when Tab 5 is opened!) ---
        from streamlit_geolocation import streamlit_geolocation
        from geopy.geocoders import Nominatim
        from PIL import Image, ImageDraw, ImageFont
        
        locked_df = base_df[(base_df['selected_gift'].notna()) & (base_df['selected_gift'].str.strip() != "")].copy()
        
        if locked_df.empty:
            st.success("🎉 All caught up! There are no locked gifts waiting for delivery.")
        else:
            if st.session_state.role == 'admin':
                districts = ["-- Select District --"] + sorted(locked_df['ParentCompanyDistrict'].dropna().unique().tolist())
                sel_dist = st.selectbox("Select District (Delivery):", districts, key="del_dist")
                if sel_dist != "-- Select District --":
                    parents = ["-- Select Parent Company --"] + sorted(locked_df[locked_df['ParentCompanyDistrict'] == sel_dist]['ParentCompanyName'].dropna().unique().tolist())
                    sel_parent = st.selectbox("Select Parent Company:", parents, key="del_parent")
                    if sel_parent != "-- Select Parent Company --":
                        companies_df = locked_df[(locked_df['ParentCompanyDistrict'] == sel_dist) & (locked_df['ParentCompanyName'] == sel_parent)]
                        comp_options = ["-- Select Company --"] + companies_df.apply(lambda row: f"{row['CompanyName']} (Mobile: {row['customermobile']})", axis=1).tolist()
                        sel_comp = st.selectbox("Select Customer to Deliver:", comp_options, key="del_comp")
                        if sel_comp != "-- Select Company --":
                            mobile_match = re.search(r"Mobile: (\d+)", sel_comp)
                            selected_del_mobile = int(mobile_match.group(1))
                        else:
                            selected_del_mobile = None
                    else:
                        selected_del_mobile = None
                else:
                    selected_del_mobile = None

            elif st.session_state.role == 'district':
                parents = ["-- Select Parent Company --"] + sorted(locked_df['ParentCompanyName'].dropna().unique().tolist())
                sel_parent = st.selectbox("Select Parent Company:", parents, key="del_parent")
                if sel_parent != "-- Select Parent Company --":
                    companies_df = locked_df[locked_df['ParentCompanyName'] == sel_parent]
                    comp_options = ["-- Select Company --"] + companies_df.apply(lambda row: f"{row['CompanyName']} (Mobile: {row['customermobile']})", axis=1).tolist()
                    sel_comp = st.selectbox("Select Customer to Deliver:", comp_options, key="del_comp")
                    if sel_comp != "-- Select Company --":
                        mobile_match = re.search(r"Mobile: (\d+)", sel_comp)
                        selected_del_mobile = int(mobile_match.group(1))
                    else:
                        selected_del_mobile = None
                else:
                    selected_del_mobile = None
                    
            elif st.session_state.role == 'parent_company':
                comp_options = ["-- Select Company --"] + locked_df.apply(lambda row: f"{row['CompanyName']} (Mobile: {row['customermobile']})", axis=1).tolist()
                sel_comp = st.selectbox("Select Customer to Deliver:", comp_options, key="del_comp")
                if sel_comp != "-- Select Company --":
                    mobile_match = re.search(r"Mobile: (\d+)", sel_comp)
                    selected_del_mobile = int(mobile_match.group(1))
                else:
                    selected_del_mobile = None

            if selected_del_mobile:
                del_data = base_df[base_df['customermobile'] == selected_del_mobile].iloc[0]
                allocated_gift = del_data.get('selected_gift', '')
                delivery_status = del_data.get('delivery_status', 'Pending')

                if delivery_status == 'Delivered':
                    st.success("✅ This customer's gift has already been delivered!")
                    if del_data.get('delivery_photo'):
                        try:
                            st.image(base64.b64decode(del_data['delivery_photo']), caption="Proof of Delivery", width=300)
                        except:
                            pass
                else:
                    st.info(f"🎁 **To Deliver:** {allocated_gift}")
                    st.markdown("### 📸 Capture Delivery Proof")
                    
                    with st.expander("👉 Tap here to Open Camera & GPS", expanded=True):
                        st.info("📍 **STEP 1:** Click the 'Get Location' button below and wait for coordinates to appear!")
                        loc = streamlit_geolocation()
                        
                        st.info("📸 **STEP 2:** Take the picture of the shop.")
                        photo = st.camera_input("Take Photo at the Shop")
                        
                        st.warning("⚠️ **EMERGENCY FALLBACK:** If the GPS button above is spinning forever or not working, type the shop address manually here:")
                        manual_address = st.text_input("Type manual address (leave blank if GPS works):")
                    
                    skip_photo = st.checkbox("🧪 Testing Mode: Save without taking a photo")

                    if st.button("Confirm & Save Delivery", use_container_width=True):
                        has_gps = loc and 'latitude' in loc and loc['latitude'] is not None
                        has_manual = len(manual_address.strip()) > 0
                        
                        if not has_gps and not has_manual:
                            st.error("📍 Please click 'Get Location', OR type a manual address in the emergency box!")
                        elif not photo and not skip_photo:
                            st.error("📸 Please take a photo, or check the 'Testing Mode' box to skip.")
                        else:
                            with st.spinner("Stamping photo and saving to database..."):
                                delivery_time = datetime.now().strftime("%Y-%m-%d %I:%M %p")
                                
                                if has_gps:
                                    lat = loc['latitude']
                                    lon = loc['longitude']
                                    try:
                                        geolocator = Nominatim(user_agent="gift_app")
                                        location_data = geolocator.reverse((lat, lon), exactly_one=True)
                                        address = location_data.address if location_data else "Address not found"
                                    except:
                                        address = "Address lookup failed"
                                    stamp_text = f"GPS: {lat}, {lon}\nTime: {delivery_time}"
                                else:
                                    lat = "Manual"
                                    lon = "Manual"
                                    address = manual_address
                                    stamp_text = f"Address: {manual_address}\nTime: {delivery_time}"
                                
                                final_photo_b64 = ""
                                if photo:
                                    try:
                                        img = Image.open(photo)
                                        draw = ImageDraw.Draw(img)
                                        
                                        try:
                                            font = ImageFont.truetype("DejaVuSans.ttf", 18)
                                        except:
                                            font = ImageFont.load_default()
                                            
                                        draw.rectangle(((0, 0), (img.width, 50)), fill="black")
                                        draw.text((10, 5), stamp_text, fill="white", font=font)
                                        
                                        buffered = io.BytesIO()
                                        img.save(buffered, format="JPEG")
                                        final_photo_b64 = base64.b64encode(buffered.getvalue()).decode()
                                    except Exception as e:
                                        st.error(f"Could not stamp photo, saving original. Error: {e}")
                                        final_photo_b64 = base64.b64encode(photo.getvalue()).decode()

                                with engine.begin() as conn:
                                    query = text("""
                                        UPDATE sales_data 
                                        SET delivery_status = 'Delivered', 
                                            delivery_photo = :photo,
                                            delivery_lat = :lat,
                                            delivery_lon = :lon,
                                            delivery_address = :addr,
                                            delivery_time = :time
                                        WHERE CAST(customermobile AS TEXT) = :mobile
                                    """)
                                    conn.execute(query, {
                                        "photo": final_photo_b64,
                                        "lat": str(lat),
                                        "lon": str(lon),
                                        "addr": address,
                                        "time": delivery_time,
                                        "mobile": str(selected_del_mobile)
                                    })
                                
                                st.success("🎉 Delivery verified and saved successfully!")
                                time.sleep(1.5)
                                load_database_data.clear()
                                st.rerun()

# --------- TAB 6: ADMIN MAP & PROOFS ---------
if tab6 is not None:
    with tab6:
        st.subheader("🗺️ Admin Delivery Map & Proofs")
        st.write("Hover over the delivery trucks to see the proof photo and address!")
        
        # --- LAZY LOADING IMPORTS (Only loads when Admin opens the Map!) ---
        import folium
        from streamlit_folium import st_folium
        
        parents_map = ["All Parent Companies"] + sorted(base_df['ParentCompanyName'].dropna().unique().tolist())
        sel_parent_map = st.selectbox("Filter Map by Parent Company:", parents_map)
        
        if sel_parent_map != "All Parent Companies":
            map_df = base_df[base_df['ParentCompanyName'] == sel_parent_map]
        else:
            map_df = base_df.copy()
            
        delivered_map_df = map_df[map_df['delivery_status'] == 'Delivered'].copy()
        pending_map_df = map_df[(map_df['selected_gift'].str.strip() != "") & (map_df['delivery_status'] != 'Delivered')]
        
        if not delivered_map_df.empty:
            delivered_map_df['Lat'] = pd.to_numeric(delivered_map_df['delivery_lat'], errors='coerce')
            delivered_map_df['Lon'] = pd.to_numeric(delivered_map_df['delivery_lon'], errors='coerce')
            delivered_map_df = delivered_map_df.dropna(subset=['Lat', 'Lon'])
            
            if not delivered_map_df.empty:
                center_lat = delivered_map_df['Lat'].mean()
                center_lon = delivered_map_df['Lon'].mean()
                
                m = folium.Map(location=[center_lat, center_lon], zoom_start=11)
                
                for idx, row in delivered_map_df.iterrows():
                    photo_b64 = row.get('delivery_photo', '')
                    addr = row.get('delivery_address', 'Address not recorded')
                    comp = row['CompanyName']
                    gift = row['selected_gift']
                    
                    if photo_b64:
                        img_html = f'<img src="data:image/jpeg;base64,{photo_b64}" style="width: 200px; border-radius: 5px; margin-top: 8px; border: 1px solid #ddd;">'
                    else:
                        img_html = '<p style="font-size:10px; color:gray; font-style:italic;">No photo available</p>'
                    
                    hover_html = f'''
                    <div style="width: 200px; font-family: sans-serif;">
                        <h4 style="color: #00b84c; margin: 0 0 5px 0;">{comp}</h4>
                        <p style="font-size: 11px; color: #555; margin: 0 0 5px 0;">📍 {addr}</p>
                        <p style="font-size: 11px; color: #333; margin: 0 0 5px 0;"><b>🎁 Gift:</b> {gift}</p>
                        {img_html}
                    </div>
                    '''
                    
                    custom_icon = folium.Icon(color="green", icon="truck", prefix="fa")
                    
                    folium.Marker(
                        location=[row['Lat'], row['Lon']],
                        icon=custom_icon,
                        tooltip=folium.Tooltip(hover_html)
                    ).add_to(m)
                
                st_folium(m, use_container_width=True, height=600, returned_objects=[])
                
            else:
                st.info("No valid GPS coordinates found to plot on the map yet.")
        else:
            st.info("No deliveries have been completed for this selection yet.")

        st.divider()
        
        st.subheader("📸 Verify Delivery Photos")
        if delivered_map_df.empty:
            st.write("No photos to review.")
        else:
            check_options = ["-- Select Delivered Customer --"] + delivered_map_df.apply(lambda row: f"{row['CompanyName']} (Mobile: {row['customermobile']})", axis=1).tolist()
            check_comp = st.selectbox("Search Customer to View Proof:", check_options, key="proof_viewer")
            
            if check_comp != "-- Select Delivered Customer --":
                check_mobile_match = re.search(r"Mobile: (\d+)", check_comp)
                check_mobile = int(check_mobile_match.group(1))
                
                proof_data = delivered_map_df[delivered_map_df['customermobile'] == check_mobile].iloc[0]
                
                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    st.write(f"**Delivered At:** {proof_data.get('delivery_time', 'Time unknown')}")
                    st.write(f"**Delivered Items:** {proof_data['selected_gift']}")
                    
                    address_text = proof_data.get('delivery_address', '')
                    if not address_text:
                        address_text = "Address not recorded."
                    st.write(f"**📍 Delivery Address:** {address_text}")
                    
                with col_p2:
                    if proof_data.get('delivery_photo'):
                        try:
                            img_data = base64.b64decode(proof_data['delivery_photo'])
                            st.image(img_data, caption=f"Delivery at {proof_data['CompanyName']}", use_container_width=True)
                        except:
                            st.error("Error loading image.")
                    else:
                        st.warning("No photo available.")

        st.divider()
        
        st.subheader(f"⏳ Pending Deliveries ({len(pending_map_df)})")
        st.write("*(Note: Pending customers cannot be shown on the map because their GPS coordinates are not captured until the delivery person arrives at their shop).*")
        
        if pending_map_df.empty:
            st.success("All locked gifts for this Parent Company have been delivered!")
        else:
            st.dataframe(pending_map_df[['ParentCompanyName', 'CompanyName', 'customermobile', 'selected_gift']], use_container_width=True)
# --------- TAB 7: PRIMARY VS SECONDARY (ADMIN ONLY) ---------
if tab7 is not None:
    with tab7:
        st.subheader("📈 Primary vs Secondary Sales Comparison")
        
        with st.expander("⚙️ Setup / Update Primary Database Table", expanded=primary_df.empty):
            st.info("Upload your 'july to march sale.xlsx' file here to build or update the database table.")
            uploaded_file = st.file_uploader("Upload Primary Data", type=['csv', 'xlsx'])
            
            if uploaded_file is not None:
                if st.button("Save to SQL Database & Clear Cache", type="primary"):
                    with st.spinner("Building table in Neon SQL..."):
                        try:
                            if uploaded_file.name.endswith('.csv'):
                                new_prim_df = pd.read_csv(uploaded_file)
                            else:
                                new_prim_df = pd.read_excel(uploaded_file)
                                
                            new_prim_df.to_sql("primary_sales", engine, if_exists="replace", index=False)
                            st.success("✅ Table 'primary_sales' successfully updated in your database!")
                            st.cache_data.clear()
                            time.sleep(1.5)
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Database error: {e}")

        st.divider()

        if primary_df is None or primary_df.empty:
            st.warning("⚠️ Primary sales data not found. Please use the tool above to upload your file.")
            if st.button("🔄 Force Refresh Database"):
                st.cache_data.clear()
                st.rerun()
        else:
            st.success(f"📊 Primary Data loaded successfully! ({len(primary_df)} rows found)")
            
            district_map = base_df[['ParentCompanyName', 'ParentCompanyDistrict']].dropna().drop_duplicates(subset=['ParentCompanyName'])
            district_map['ParentCompanyName'] = district_map['ParentCompanyName'].astype(str).str.upper().str.strip()

            sec_sales = base_df.groupby('ParentCompanyName')['Total'].sum().reset_index()
            sec_sales.columns = ['ParentCompanyName', 'Live_Secondary_Sales']
            
            st.info("⚙️ Map the columns from your Primary Database to configure the report:")
            col_map1, col_map2 = st.columns(2)
            
            with col_map1:
                dist_col = st.selectbox("Which column represents the Distributor?", primary_df.columns.tolist())
            with col_map2:
                numeric_cols = primary_df.select_dtypes(include=['number']).columns.tolist()
                if not numeric_cols:
                    numeric_cols = primary_df.columns.tolist()
                val_col = st.selectbox("Which column represents the Primary Sales Value?", numeric_cols)
                
            prim_sales = primary_df.groupby(dist_col)[val_col].sum().reset_index()
            
            prim_sales[dist_col] = prim_sales[dist_col].astype(str).str.upper().str.strip()
            sec_sales['ParentCompanyName'] = sec_sales['ParentCompanyName'].astype(str).str.upper().str.strip()
            prim_sales.columns = ['ParentCompanyName', 'Static_Primary_Sales']
            
            comparison_df = pd.merge(prim_sales, sec_sales, on='ParentCompanyName', how='outer').fillna(0)
            comparison_df = pd.merge(comparison_df, district_map, on='ParentCompanyName', how='left')
            comparison_df['ParentCompanyDistrict'] = comparison_df['ParentCompanyDistrict'].fillna("Unknown")
            
            comparison_df['Variance (Excess/Deficit)'] = comparison_df['Live_Secondary_Sales'] - comparison_df['Static_Primary_Sales']
            
            comparison_df = comparison_df[['ParentCompanyDistrict', 'ParentCompanyName', 'Static_Primary_Sales', 'Live_Secondary_Sales', 'Variance (Excess/Deficit)']]
            comparison_df.rename(columns={'ParentCompanyDistrict': 'District'}, inplace=True)

            st.divider()

            all_districts = sorted(comparison_df['District'].unique().tolist())
            selected_dist = st.selectbox("🔍 Filter Report by District:", ["All Districts"] + all_districts)

            if selected_dist != "All Districts":
                display_df = comparison_df[comparison_df['District'] == selected_dist].copy()
            else:
                display_df = comparison_df.copy()

            total_prim = display_df['Static_Primary_Sales'].sum()
            total_sec = display_df['Live_Secondary_Sales'].sum()
            total_var = display_df['Variance (Excess/Deficit)'].sum()

            st.markdown(f"### 📈 Grand Totals for: **{selected_dist}**")
            met1, met2, met3 = st.columns(3)
            met1.metric("Grand Total Primary Sales", f"₹ {total_prim:,.2f}")
            met2.metric("Grand Total Secondary Sales", f"₹ {total_sec:,.2f}")
            met3.metric("Grand Total Variance", f"₹ {total_var:,.2f}")
            
            st.write("") 

            st.dataframe(
                display_df, 
                use_container_width=True,
                column_config={
                    "District": st.column_config.TextColumn("District"),
                    "ParentCompanyName": st.column_config.TextColumn("Distributor"),
                    "Static_Primary_Sales": st.column_config.NumberColumn("Primary Sales", format="₹ %.2f"),
                    "Live_Secondary_Sales": st.column_config.NumberColumn("Secondary Sales", format="₹ %.2f"),
                    "Variance (Excess/Deficit)": st.column_config.NumberColumn("Variance", format="₹ %.2f")
                }
            )
            
            csv_export = display_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Data as CSV (Numeric Format for Excel)", 
                data=csv_export, 
                file_name=f"Primary_vs_Secondary_{selected_dist.replace(' ', '_')}.csv", 
                mime="text/csv",
                type="primary"
            )
            
            st.markdown(f"### Top 10 Distributors ({selected_dist})")
            chart_data = display_df.sort_values('Live_Secondary_Sales', ascending=False).head(10)
            st.bar_chart(data=chart_data.set_index('ParentCompanyName')[['Static_Primary_Sales', 'Live_Secondary_Sales']])

# --------- TAB 7: PRIMARY VS SECONDARY (ADMIN ONLY) ---------
if tab7 is not None:
    with tab7:
        st.subheader("📈 Primary vs Secondary Sales Comparison")
        
        with st.expander("⚙️ Setup / Update Primary Database Table", expanded=primary_df.empty):
            st.info("Upload your 'july to march sale.xlsx' file here to build or update the database table.")
            uploaded_file = st.file_uploader("Upload Primary Data", type=['csv', 'xlsx'], key="tab7_master_uploader")
            
            if uploaded_file is not None:
                if st.button("Save to SQL Database & Clear Cache", type="primary", key="tab7_save_btn"):
                    with st.spinner("Building table in Neon SQL..."):
                        try:
                            if uploaded_file.name.endswith('.csv'):
                                new_prim_df = pd.read_csv(uploaded_file)
                            else:
                                new_prim_df = pd.read_excel(uploaded_file)
                                
                            new_prim_df.to_sql("primary_sales", engine, if_exists="replace", index=False)
                            st.success("✅ Table 'primary_sales' successfully updated in your database!")
                            st.cache_data.clear()
                            time.sleep(1.5)
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Database error: {e}")

        st.divider()

        if primary_df is None or primary_df.empty:
            st.warning("⚠️ Primary sales data not found. Please use the tool above to upload your file.")
            if st.button("🔄 Force Refresh Database", key="tab7_refresh_btn"):
                st.cache_data.clear()
                st.rerun()
        else:
            st.success(f"📊 Primary Data loaded successfully! ({len(primary_df)} rows found)")
            
            district_map = base_df[['ParentCompanyName', 'ParentCompanyDistrict']].dropna().drop_duplicates(subset=['ParentCompanyName'])
            district_map['ParentCompanyName'] = district_map['ParentCompanyName'].astype(str).str.upper().str.strip()

            if 'is_blocked' in base_df.columns:
                active_base_df = base_df[base_df['is_blocked'] != 'Yes'].copy()
            else:
                active_base_df = base_df.copy()

            sec_sales = active_base_df.groupby('ParentCompanyName')['Total'].sum().reset_index()
            sec_sales.columns = ['ParentCompanyName', 'Live_Secondary_Sales']
            
            st.info("⚙️ Map the columns from your Primary Database to configure the report:")
            col_map1, col_map2 = st.columns(2)
            
            with col_map1:
                dist_col = st.selectbox("Which column represents the Distributor?", primary_df.columns.tolist(), key="tab7_dist_mapper")
            with col_map2:
                numeric_cols = primary_df.select_dtypes(include=['number']).columns.tolist()
                if not numeric_cols:
                    numeric_cols = primary_df.columns.tolist()
                val_col = st.selectbox("Which column represents the Primary Sales Value?", numeric_cols, key="tab7_val_mapper")
                
            prim_sales = primary_df.groupby(dist_col)[val_col].sum().reset_index()
            
            prim_sales[dist_col] = prim_sales[dist_col].astype(str).str.upper().str.strip()
            sec_sales['ParentCompanyName'] = sec_sales['ParentCompanyName'].astype(str).str.upper().str.strip()
            prim_sales.columns = ['ParentCompanyName', 'Static_Primary_Sales']
            
            comparison_df = pd.merge(prim_sales, sec_sales, on='ParentCompanyName', how='outer').fillna(0)
            comparison_df = pd.merge(comparison_df, district_map, on='ParentCompanyName', how='left')
            comparison_df['ParentCompanyDistrict'] = comparison_df['ParentCompanyDistrict'].fillna("Unknown")
            
            comparison_df['Variance (Excess/Deficit)'] = comparison_df['Live_Secondary_Sales'] - comparison_df['Static_Primary_Sales']
            
            comparison_df = comparison_df[['ParentCompanyDistrict', 'ParentCompanyName', 'Static_Primary_Sales', 'Live_Secondary_Sales', 'Variance (Excess/Deficit)']]
            comparison_df.rename(columns={'ParentCompanyDistrict': 'District'}, inplace=True)

            st.divider()

            all_districts = sorted(comparison_df['District'].unique().tolist())
            selected_dist = st.selectbox("🔍 Filter Report by District:", ["All Districts"] + all_districts, key="tab7_district_filter")

            if selected_dist != "All Districts":
                display_df = comparison_df[comparison_df['District'] == selected_dist].copy()
            else:
                display_df = comparison_df.copy()

            total_prim = display_df['Static_Primary_Sales'].sum()
            total_sec = display_df['Live_Secondary_Sales'].sum()
            total_var = display_df['Variance (Excess/Deficit)'].sum()

            st.markdown(f"### 📈 Grand Totals for: **{selected_dist}**")
            met1, met2, met3 = st.columns(3)
            met1.metric("Grand Total Primary Sales", f"₹ {total_prim:,.2f}")
            met2.metric("Grand Total Secondary Sales", f"₹ {total_sec:,.2f}")
            met3.metric("Grand Total Variance", f"₹ {total_var:,.2f}")
            
            st.write("") 

            st.dataframe(
                display_df, 
                use_container_width=True,
                column_config={
                    "District": st.column_config.TextColumn("District"),
                    "ParentCompanyName": st.column_config.TextColumn("Distributor"),
                    "Static_Primary_Sales": st.column_config.NumberColumn("Primary Sales", format="₹ %.2f"),
                    "Live_Secondary_Sales": st.column_config.NumberColumn("Secondary Sales", format="₹ %.2f"),
                    "Variance (Excess/Deficit)": st.column_config.NumberColumn("Variance", format="₹ %.2f")
                }
            )
            
            csv_export = display_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Data as CSV (Numeric Format for Excel)", 
                data=csv_export, 
                file_name=f"Primary_vs_Secondary_{selected_dist.replace(' ', '_')}.csv", 
                mime="text/csv",
                type="primary",
                key="tab7_download_btn"
            )
            
            st.markdown(f"### Top 10 Distributors ({selected_dist})")
            chart_data = display_df.sort_values('Live_Secondary_Sales', ascending=False).head(10)
            st.bar_chart(data=chart_data.set_index('ParentCompanyName')[['Static_Primary_Sales', 'Live_Secondary_Sales']])


