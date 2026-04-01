import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import os
import re
import time
import base64
from datetime import datetime
from geopy.geocoders import Nominatim
import folium
from streamlit_folium import st_folium
from streamlit_geolocation import streamlit_geolocation
from PIL import Image, ImageDraw, ImageFont
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
    return create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

engine = init_connection()

# This tells Streamlit to only download the data once every 10 minutes!
@st.cache_data(ttl=600)
def load_database_data():
    with engine.connect() as conn:
        cust = pd.read_sql("SELECT * FROM sales_data", conn)
        gfts = pd.read_sql("SELECT * FROM gift_slabs", conn)
        try:
            csts = pd.read_sql("SELECT * FROM slab_costs", conn)
        except Exception:
            csts = pd.DataFrame(columns=['SLAB', 'COST'])
    return cust, gfts, csts

try:
    # We load the cached data, then create a copy so we can safely edit it
    customers_raw, gifts_raw, costs_df = load_database_data()
    customers = customers_raw.copy()
    gifts = gifts_raw.copy()
    
    slab_to_cost = {float(row['SLAB']): float(row['COST']) for _, row in costs_df.iterrows()}
    
    gifts['SLAB'] = pd.to_numeric(gifts['SLAB'], errors='coerce')
    gifts.loc[gifts['SLAB'] == 10000000, 'SLAB'] = 1000000
    
    # Check and add necessary delivery columns
    columns_to_add = {
        'selected_gift': "TEXT",
        'delivery_status': "TEXT DEFAULT 'Pending'",
        'delivery_photo': "TEXT",
        'delivery_lat': "TEXT",
        'delivery_lon': "TEXT",
        'delivery_address': "TEXT",
        'delivery_time': "TEXT"
    }
    
    for col, col_type in columns_to_add.items():
        if col not in customers.columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE sales_data ADD COLUMN {col} {col_type}"))
            if 'DEFAULT' in col_type:
                customers[col] = "Pending"
            else:
                customers[col] = ""
        else:
            if col == 'delivery_status':
                customers[col] = customers[col].fillna("Pending")
            else:
                customers[col] = customers[col].fillna("")
        
except Exception as e:
    st.error(f"Database connection failed. Details: {e}")
    st.stop()

# --- 2. URL PARAMETER LOGIN SYSTEM (FIXES REFRESH LOGOUT) ---
if 'logged_in' not in st.session_state:
    # Check the web address bar to see if they already logged in
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
                # Set URL parameters
                st.query_params["role"] = "admin"
                st.query_params["scope"] = "ALL"
                st.query_params["user"] = "Admin"
                # THE FIX: Instantly update the internal session memory
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
st.sidebar.title(f"Welcome, {st.session_state.username}")
st.sidebar.markdown(f"**Role:** {st.session_state.role.title()}")

if st.sidebar.button("Log Out"):
    # Clear the URL parameters and session
    st.query_params.clear()
    st.session_state.clear()
    st.rerun()

st.title("🎁 Gift Allocation Dashboard")

# --- DEFINE BASE_DF BASED ON USER ROLE ---
if st.session_state.role == 'admin':
    base_df = customers.copy()
elif st.session_state.role == 'district':
    base_df = customers[customers['ParentCompanyDistrict'] == st.session_state.scope].copy()
elif st.session_state.role == 'parent_company':
    base_df = customers[customers['pcidd'] == st.session_state.scope].copy()
else:
    base_df = pd.DataFrame() # Safety fallback
            
# Dynamic Tabs based on Role
if st.session_state.role == 'admin':
    # Admin gets the exclusive Map Tab instead of standard proof checking
    tabs = st.tabs(["🎁 Allocate Gifts", "📊 Customer Wise Report", "📦 Projected Breakdown", "🛍️ Locked Gifts Breakdown", "🚚 Deliver Gifts", "🗺️ Admin Map & Proofs"])
    tab1, tab2, tab3, tab4, tab5, tab6 = tabs[0], tabs[1], tabs[2], tabs[3], tabs[4], tabs[5]
elif st.session_state.role == 'district':
    tabs = st.tabs(["🎁 Allocate Gifts", "📊 Customer Wise Report", "📦 Projected Breakdown", "🛍️ Locked Gifts Breakdown", "🚚 Deliver Gifts"])
    tab1, tab2, tab3, tab4, tab5 = tabs[0], tabs[1], tabs[2], tabs[3], tabs[4]
    tab6 = None
else:
    tabs = st.tabs(["🎁 Allocate Gifts", "📊 Customer Wise Report", "🛍️ Locked Gifts Breakdown", "🚚 Deliver Gifts"])
    tab1, tab2, tab4, tab5 = tabs[0], tabs[1], tabs[2], tabs[3]
    tab3 = None
    tab6 = None

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
        
        st.info(f"🪙 **Available Points:** {customer_points} | **Credit Limit:** {customer_data['CreditLimit']}")

        if current_allocation and str(current_allocation).strip() != "":
            st.success(f"🔒 **Gift Locked:** {current_allocation}")
            
            if delivery_status == 'Delivered':
                st.success("✅ **STATUS: DELIVERED** - This gift has already been handed over to the customer.")
            else:
                if st.session_state.role == 'admin':
                    if st.button("Revoke / Change Allocation (Admin Only)"):
                        with engine.begin() as conn:
                            query = text("UPDATE sales_data SET selected_gift = '' WHERE customermobile = :mobile")
                            conn.execute(query, {"mobile": selected_mobile})
                        
                        load_database_data.clear() # <--- FIX 1: Clears memory when an Admin revokes a gift
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
                            query = text("UPDATE sales_data SET selected_gift = :gift, delivery_status = 'Pending' WHERE customermobile = :mobile")
                            conn.execute(query, {"gift": final_gift_string, "mobile": selected_mobile})
                        st.success(f"🎉 Successfully locked in: **{final_gift_string}**!")
                        st.balloons()
                        time.sleep(1.5)
                        
                        load_database_data.clear() # <--- FIX 2: Clears memory when a user locks a new gift
                        st.rerun()
# --------- TAB 2: CUSTOMER WISE REPORT ---------
with tab2:
    st.subheader("📊 Customer Wise Report")
    report_df = base_df.copy()
    display_cols = ['ParentCompanyDistrict', 'ParentCompanyName', 'CompanyName', 'customermobile', 'Total', 'selected_gift', 'delivery_status', 'delivery_time']
    
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
        st.dataframe(report_df[display_cols], use_container_width=True)

    elif st.session_state.role == 'district':
        parent_filter = st.selectbox("Select Parent Company:", ["All Parent Companies"] + sorted(report_df['ParentCompanyName'].dropna().unique().tolist()))
        if parent_filter != "All Parent Companies":
            report_df = report_df[report_df['ParentCompanyName'] == parent_filter]
        st.dataframe(report_df[display_cols], use_container_width=True)

    elif st.session_state.role == 'parent_company':
        st.dataframe(report_df[display_cols], use_container_width=True)

    if not report_df.empty:
        csv = report_df[display_cols].to_csv(index=False).encode('utf-8')
        st.download_button(label="Download Report as CSV", data=csv, file_name=f"customer_report_{st.session_state.username}.csv", mime="text/csv")

# --------- TAB 3: SLAB WISE REPORT (Projected) ---------
if tab3 is not None:
    with tab3:
        st.subheader("📦 Projected Slab Breakdown")
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
                row_data = {"Slab": int(slab), "Gift Name": g_name, "Quantity": count}
                if st.session_state.role == 'admin':
                    unit_cost = slab_to_cost.get(slab, 0)
                    total_spend = count * unit_cost
                    total_slab_value = count * float(slab)
                    pct_of_grand = (total_spend / grand_total_spend * 100) if grand_total_spend > 0 else 0
                    reward_pct = (total_spend / total_slab_value * 100) if total_slab_value > 0 else 0
                    row_data["Total Slab Value"] = f"{total_slab_value:,.0f}"
                    row_data["Unit Cost (₹)"] = f"{unit_cost:,.2f}"
                    row_data["Total Spend (₹)"] = f"{total_spend:,.2f}"
                    row_data["Reward %"] = f"{reward_pct:.2f}%"
                    row_data["% of Grand Total"] = f"{pct_of_grand:.2f}%"
                summary_data.append(row_data)

            st.dataframe(pd.DataFrame(summary_data), use_container_width=True)
            
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

# --------- TAB 5: DELIVER GIFTS ---------
if tab5 is not None:
    with tab5:
        st.subheader("🚚 Deliver Gifts")
        
        # --- THE FIX: Filter data to ONLY show customers with locked gifts ---
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
                    
                    with st.expander("👉 Tap here to Open Camera & GPS", expanded=False):
                        loc = streamlit_geolocation()
                        photo = st.camera_input("Take Photo at the Shop")
                    
                    skip_photo = st.checkbox("🧪 Testing Mode: Save without taking a photo")

                    if st.button("Confirm & Save Delivery", use_container_width=True):
                        if not loc or 'latitude' not in loc:
                            st.error("📍 Please wait for the GPS location to load before confirming!")
                        elif not photo and not skip_photo:
                            st.error("📸 Please take a photo, or check the 'Testing Mode' box to skip.")
                        else:
                            with st.spinner("Stamping photo and saving to database..."):
                                lat = loc['latitude']
                                lon = loc['longitude']
                                delivery_time = datetime.now().strftime("%Y-%m-%d %I:%M %p")
                                
                                try:
                                    geolocator = Nominatim(user_agent="gift_app")
                                    location_data = geolocator.reverse((lat, lon), exactly_one=True)
                                    address = location_data.address if location_data else "Address not found"
                                except:
                                    address = "Address lookup failed"
                                
                                final_photo_b64 = ""
                                if photo:
                                    try:
                                        img = Image.open(photo)
                                        draw = ImageDraw.Draw(img)
                                        
                                        try:
                                            font = ImageFont.truetype("DejaVuSans.ttf", 18)
                                        except:
                                            font = ImageFont.load_default()
                                            
                                        stamp_text = f"GPS: {lat}, {lon}\nTime: {delivery_time}"
                                        
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
                                        WHERE customermobile = :mobile
                                    """)
                                    conn.execute(query, {
                                        "photo": final_photo_b64,
                                        "lat": str(lat),
                                        "lon": str(lon),
                                        "addr": address,
                                        "time": delivery_time,
                                        "mobile": selected_del_mobile
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
        
        # 1. Map Filter
        parents_map = ["All Parent Companies"] + sorted(base_df['ParentCompanyName'].dropna().unique().tolist())
        sel_parent_map = st.selectbox("Filter Map by Parent Company:", parents_map)
        
        if sel_parent_map != "All Parent Companies":
            map_df = base_df[base_df['ParentCompanyName'] == sel_parent_map]
        else:
            map_df = base_df.copy()
            
        delivered_map_df = map_df[map_df['delivery_status'] == 'Delivered'].copy()
        pending_map_df = map_df[(map_df['selected_gift'].str.strip() != "") & (map_df['delivery_status'] != 'Delivered')]
        
        # 2. Render the Custom Folium Map
        if not delivered_map_df.empty:
            delivered_map_df['Lat'] = pd.to_numeric(delivered_map_df['delivery_lat'], errors='coerce')
            delivered_map_df['Lon'] = pd.to_numeric(delivered_map_df['delivery_lon'], errors='coerce')
            delivered_map_df = delivered_map_df.dropna(subset=['Lat', 'Lon'])
            
            if not delivered_map_df.empty:
                # Center the map automatically
                center_lat = delivered_map_df['Lat'].mean()
                center_lon = delivered_map_df['Lon'].mean()
                
                # Create the base map
                m = folium.Map(location=[center_lat, center_lon], zoom_start=11)
                
                # Plot each delivery truck
                for idx, row in delivered_map_df.iterrows():
                    photo_b64 = row.get('delivery_photo', '')
                    addr = row.get('delivery_address', 'Address not recorded')
                    comp = row['CompanyName']
                    gift = row['selected_gift']
                    
                    # Create the Image HTML if the photo exists
                    if photo_b64:
                        img_html = f'<img src="data:image/jpeg;base64,{photo_b64}" style="width: 200px; border-radius: 5px; margin-top: 8px; border: 1px solid #ddd;">'
                    else:
                        img_html = '<p style="font-size:10px; color:gray; font-style:italic;">No photo available</p>'
                    
                    # Build the Hover Card
                    hover_html = f'''
                    <div style="width: 200px; font-family: sans-serif;">
                        <h4 style="color: #00b84c; margin: 0 0 5px 0;">{comp}</h4>
                        <p style="font-size: 11px; color: #555; margin: 0 0 5px 0;">📍 {addr}</p>
                        <p style="font-size: 11px; color: #333; margin: 0 0 5px 0;"><b>🎁 Gift:</b> {gift}</p>
                        {img_html}
                    </div>
                    '''
                    
                    # Create the Delivery Truck Icon
                    custom_icon = folium.Icon(color="green", icon="truck", prefix="fa")
                    
                    # Add to map
                    folium.Marker(
                        location=[row['Lat'], row['Lon']],
                        icon=custom_icon,
                        tooltip=folium.Tooltip(hover_html)
                    ).add_to(m)
                
                # Render the map in Streamlit (returned_objects=[] makes it run much faster)
                st_folium(m, use_container_width=True, height=600, returned_objects=[])
                
            else:
                st.info("No valid GPS coordinates found to plot on the map yet.")
        else:
            st.info("No deliveries have been completed for this selection yet.")

        st.divider()
        
        # 3. Quick Proof Verification Tool
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
        
        # 4. Pending Reminder List
        st.subheader(f"⏳ Pending Deliveries ({len(pending_map_df)})")
        st.write("*(Note: Pending customers cannot be shown on the map because their GPS coordinates are not captured until the delivery person arrives at their shop).*")
        
        if pending_map_df.empty:
            st.success("All locked gifts for this Parent Company have been delivered!")
        else:
            st.dataframe(pending_map_df[['ParentCompanyName', 'CompanyName', 'customermobile', 'selected_gift']], use_container_width=True)
