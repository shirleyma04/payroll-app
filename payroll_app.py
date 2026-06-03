import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pandas as pd
from pathlib import Path
from datetime import datetime
import threading
import sys
import os
import re

# ============= PAYROLL PROCESSOR CODE (embedded) =============

DEFAULT_ROLE_PERCENTAGES = {
    "Busser": 0.42,
    "Food Runner": 0.19,
    "Food-Bar Runner": 0.29,
    "Food-Bar Prep": 0.045,
    "Cashier/Host": 0.03,
    "Bartender": 0.025
}

DEFAULT_TIPOUT_PERCENTAGE = 0.07

ROLE_PERCENTAGES = DEFAULT_ROLE_PERCENTAGES.copy()

def clean_money(value):
    if pd.isna(value):
        return 0.0
    value = str(value)
    value = value.replace('$', '').replace(',', '').replace('%', '').strip()
    try:
        return float(value)
    except:
        return 0.0

def get_last_initial(name):
    if pd.isna(name):
        return ""
    name = str(name).strip()
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}"
    elif len(parts) == 1:
        return parts[0]
    else:
        return ""

def normalize_name_for_matching(df, name_column='Name'):
    df['Name_Match'] = df[name_column].apply(get_last_initial)
    return df

def get_base_pay_only(role):
    role_lower = str(role).lower()
    base_only_roles = ['server trainee', 'host trainee', 'busser trainee', 
                       'dish washer', 'prep cook']
    for base_role in base_only_roles:
        if base_role in role_lower:
            return True
    return False

def calculate_breaks_from_timecard(timecard_df):
    timecard_df.columns = [c.strip() for c in timecard_df.columns]
    required_cols = ['Name', 'Role', 'Clock In', 'Clock Out']
    for col in required_cols:
        if col not in timecard_df.columns:
            raise Exception(f'Missing required column in Time Card file: {col}')
    
    timecard_df['Clock In'] = pd.to_datetime(timecard_df['Clock In'], format='%I:%M %p', errors='coerce')
    timecard_df['Clock Out'] = pd.to_datetime(timecard_df['Clock Out'], format='%I:%M %p', errors='coerce')
    
    if 'Total Hours Worked (h)' not in timecard_df.columns:
        timecard_df['Total Hours Worked (h)'] = (timecard_df['Clock Out'] - timecard_df['Clock In']).dt.total_seconds() / 3600
        timecard_df['Total Hours Worked (h)'] = timecard_df['Total Hours Worked (h)'].fillna(0)
    
    has_unpaid_break = 'Unpaid Break (h)' in timecard_df.columns
    
    timecard_df['Break Minutes'] = 0.0
    timecard_df['Break Count'] = 0
    timecard_df['Break Hours'] = 0.0
    timecard_df['Break Already Subtracted'] = False
    
    for idx, row in timecard_df.iterrows():
        clock_in = row['Clock In']
        clock_out = row['Clock Out']
        
        if pd.isna(clock_in) or pd.isna(clock_out):
            continue
        
        if has_unpaid_break and pd.notna(row.get('Unpaid Break (h)')) and row['Unpaid Break (h)'] != '':
            try:
                break_hours = float(row['Unpaid Break (h)'])
                if break_hours > 0:
                    timecard_df.at[idx, 'Break Minutes'] = break_hours * 60
                    timecard_df.at[idx, 'Break Count'] = 1
                    timecard_df.at[idx, 'Break Hours'] = break_hours
                    timecard_df.at[idx, 'Break Already Subtracted'] = True
                    continue
            except:
                pass
        
        clock_in_hour = clock_in.hour + clock_in.minute / 60
        shift_hours = float(row['Total Hours Worked (h)'])

        if shift_hours < 3:
            timecard_df.at[idx, 'Break Minutes'] = 0.0
            timecard_df.at[idx, 'Break Count'] = 0
            timecard_df.at[idx, 'Break Hours'] = 0.0

        elif clock_in_hour < 13 and clock_out.hour >= 21:
            timecard_df.at[idx, 'Break Minutes'] = 40.0
            timecard_df.at[idx, 'Break Count'] = 2
            timecard_df.at[idx, 'Break Hours'] = 40.0 / 60.0

        else:
            timecard_df.at[idx, 'Break Minutes'] = 20.0
            timecard_df.at[idx, 'Break Count'] = 1
            timecard_df.at[idx, 'Break Hours'] = 20.0 / 60.0
        
        timecard_df.at[idx, 'Break Already Subtracted'] = False
    
    timecard_df['Adjusted Hours'] = timecard_df.apply(
        lambda row: row['Total Hours Worked (h)'] if row['Break Already Subtracted']
        else max(0.0, float(row['Total Hours Worked (h)']) - float(row['Break Hours'])),
        axis=1
    )
    
    timecard_df['Total Hours Worked (h)'] = timecard_df['Total Hours Worked (h)'].astype(float)
    timecard_df['Break Count'] = timecard_df['Break Count'].astype(int)
    timecard_df['Break Hours'] = timecard_df['Break Hours'].astype(float)
    timecard_df['Adjusted Hours'] = timecard_df['Adjusted Hours'].astype(float)
    
    timecard_agg = timecard_df.groupby(['Name', 'Role'], as_index=False).agg({
        'Total Hours Worked (h)': 'sum',
        'Break Count': 'sum',
        'Break Hours': 'sum',
        'Adjusted Hours': 'sum'
    })
    
    timecard_agg.columns = ['Name', 'Role', 'Raw Hours', 'No. of Breaks', 'Total Break Time', 'Total Hours Worked']
    timecard_agg['No. of Breaks'] = timecard_agg['No. of Breaks'].astype(int)
    timecard_agg['Total Break Time'] = timecard_agg['Total Break Time'].round(2)
    timecard_agg['Total Hours Worked'] = timecard_agg['Total Hours Worked'].round(2)
    timecard_agg['Raw Hours'] = timecard_agg['Raw Hours'].round(2)
    
    return timecard_agg

def process_payroll(
    productivity_file,
    labor_file,
    timecard_file,
    percentages=None,
    tipout_percentage=DEFAULT_TIPOUT_PERCENTAGE
):
    if percentages is None:
        percentages = ROLE_PERCENTAGES

    # Read the first line of productivity CSV to get date range
    with open(productivity_file, 'r', encoding='utf-8-sig') as f:
        first_line = f.readline().strip()
    
    # Extract date range text
    date_range_text = first_line
    if date_range_text.startswith('Date Range:'):
        date_range_text = date_range_text.replace('Date Range:', '').strip()
    
    # Read the actual data (skip the first row)
    productivity_df = pd.read_csv(productivity_file, skiprows=1)
    
    if timecard_file.endswith('.xlsx'):
        timecard_df = pd.read_excel(timecard_file, skiprows=1)
    else:
        timecard_df = pd.read_csv(timecard_file, skiprows=1)
    
    if labor_file.endswith('.xlsx'):
        labor_df = pd.read_excel(labor_file, skiprows=1)
    else:
        labor_df = pd.read_csv(labor_file, skiprows=1)

    productivity_df.columns = [c.strip() for c in productivity_df.columns]
    labor_df.columns = [c.strip() for c in labor_df.columns]
    
    labor_df.columns = (labor_df.columns.str.strip().str.replace(r"\s*\(.*?\)", "", regex=True).str.replace(r"\s*\(h\)", "", regex=True))

    productivity_required = ['Name', 'Role', 'Gross Sales', 'Service Tips']
    labor_required = ['Name', 'Role', 'Hourly Rate']
    
    for col in productivity_required:
        if col not in productivity_df.columns:
            raise Exception(f'Missing column in Productivity CSV: {col}')
    
    for col in labor_required:
        if col not in labor_df.columns:
            raise Exception(f'Missing column in Labor file: {col}')

    productivity_df['Gross Sales'] = productivity_df['Gross Sales'].apply(clean_money)
    if 'Net Sales' in productivity_df.columns:
        productivity_df['Net Sales'] = productivity_df['Net Sales'].apply(clean_money)
    else:
        productivity_df['Net Sales'] = productivity_df['Gross Sales']
    
    productivity_df['Service Tips'] = productivity_df['Service Tips'].apply(clean_money)
    labor_df['Hourly Rate'] = labor_df['Hourly Rate'].apply(clean_money)
    
    timecard_agg = calculate_breaks_from_timecard(timecard_df)
    timecard_agg = timecard_agg.drop_duplicates(subset=['Name', 'Role'], keep='first')
    
    timecard_agg = normalize_name_for_matching(timecard_agg, 'Name')
    productivity_df = normalize_name_for_matching(productivity_df, 'Name')
    labor_df = normalize_name_for_matching(labor_df, 'Name')
    
    timecard_agg['Original_Name'] = timecard_agg['Name']
    
    merged = pd.merge(
        timecard_agg,
        productivity_df[['Name_Match', 'Role', 'Gross Sales', 'Net Sales', 'Service Tips']],
        on=['Name_Match', 'Role'],
        how='left',
        suffixes=('', '_prod')
    )
    
    merged = pd.merge(
        merged,
        labor_df[['Name_Match', 'Role', 'Hourly Rate']],
        on=['Name_Match', 'Role'],
        how='left',
        suffixes=('', '_labor')
    )
    
    merged['Name'] = merged['Original_Name']
    
    merged['Gross Sales'] = merged['Gross Sales'].fillna(0)
    merged['Net Sales'] = merged['Net Sales'].fillna(0)
    merged['Service Tips'] = merged['Service Tips'].fillna(0)
    merged['Hourly Rate'] = merged['Hourly Rate'].fillna(0)
    
    merged = merged.drop_duplicates(subset=['Name', 'Role'], keep='first')
    
    merged['Estimated Total Pay'] = merged['Total Hours Worked'] * merged['Hourly Rate']
    merged['Estimated Total Pay'] = merged['Estimated Total Pay'].round(2)
    
    merged['Tip Out'] = 0.0
    merged['Gross Tips'] = 0.0
    merged['Merchant Fee'] = 0.0
    merged['Total Tips'] = 0.0
    merged['Subtotal'] = 0.0
    merged['Tip-Out Tips'] = 0.0
    merged['Final Pay'] = 0.0
    merged['Effective Hourly Rate'] = 0.0

    for idx, row in merged.iterrows():
        if get_base_pay_only(row['Role']):
            merged.at[idx, 'Final Pay'] = row['Estimated Total Pay']
            merged.at[idx, 'Effective Hourly Rate'] = row['Estimated Total Pay'] / row['Total Hours Worked'] if row['Total Hours Worked'] > 0 else 0
            merged.at[idx, 'Tip Out'] = 0
            merged.at[idx, 'Service Tips'] = 0
            merged.at[idx, 'Merchant Fee'] = 0

    server_mask = merged['Role'].str.contains('Server', case=False, na=False) & ~merged['Role'].str.contains('Trainee', case=False, na=False)
    
    merged.loc[server_mask, 'Tip Out'] = merged.loc[server_mask, 'Gross Sales'] * tipout_percentage
    merged.loc[server_mask, 'Gross Tips'] = merged.loc[server_mask, 'Service Tips'] - merged.loc[server_mask, 'Tip Out']
    merged.loc[server_mask, 'Merchant Fee'] = merged.loc[server_mask, 'Gross Tips'].apply(lambda x: max(0, x * 0.03))
    merged.loc[server_mask, 'Total Tips'] = merged.loc[server_mask, 'Gross Tips'] - merged.loc[server_mask, 'Merchant Fee']
    merged.loc[server_mask, 'Subtotal'] = merged.loc[server_mask, 'Estimated Total Pay'] + merged.loc[server_mask, 'Total Tips']
    
    total_tip_out = merged['Tip Out'].sum()
    total_merchant_fee = merged['Merchant Fee'].sum()
    total_pool = total_tip_out - total_merchant_fee

    role_pool_money = {role: total_pool * pct for role, pct in percentages.items()}
    
    role_hours = {}
    for role in percentages.keys():
        if role == "Food Runner":
            mask = merged['Role'].str.lower() == 'food runner'
        elif role == "Food-Bar Runner":
            mask = merged['Role'].str.lower() == 'food-bar runner'
        elif role == "Food-Bar Prep":
            mask = merged['Role'].str.lower() == 'food-bar prep'
        elif role == "Cashier/Host":
            mask = (merged['Role'].str.lower().str.contains('cashier', na=False) | 
                   merged['Role'].str.lower().str.contains('host', na=False)) & \
                   ~merged['Role'].str.lower().str.contains('trainee', na=False)
        else:
            mask = merged['Role'].str.contains(role, case=False, na=False) & \
                   ~merged['Role'].str.contains('Trainee', case=False, na=False)
        
        role_hours[role] = merged.loc[mask, 'Total Hours Worked'].sum()
    
    hourly_tip_rates = {}
    for role in percentages.keys():
        if role_hours[role] > 0:
            hourly_tip_rates[role] = role_pool_money[role] / role_hours[role]
        else:
            hourly_tip_rates[role] = 0

    for idx, row in merged.iterrows():
        if get_base_pay_only(row['Role']):
            continue
            
        employee_role = str(row['Role']).lower()
        
        if employee_role == 'food runner':
            merged.at[idx, 'Tip-Out Tips'] = hourly_tip_rates['Food Runner'] * row['Total Hours Worked']
        elif employee_role == 'food-bar runner':
            merged.at[idx, 'Tip-Out Tips'] = hourly_tip_rates['Food-Bar Runner'] * row['Total Hours Worked']
        elif employee_role == 'food-bar prep':
            merged.at[idx, 'Tip-Out Tips'] = hourly_tip_rates['Food-Bar Prep'] * row['Total Hours Worked']
        elif 'busser' in employee_role and 'trainee' not in employee_role:
            merged.at[idx, 'Tip-Out Tips'] = hourly_tip_rates['Busser'] * row['Total Hours Worked']
        elif ('cashier' in employee_role or 'host' in employee_role) and 'trainee' not in employee_role:
            merged.at[idx, 'Tip-Out Tips'] = hourly_tip_rates['Cashier/Host'] * row['Total Hours Worked']
        elif 'bartender' in employee_role:
            merged.at[idx, 'Tip-Out Tips'] = hourly_tip_rates['Bartender'] * row['Total Hours Worked']

    for idx, row in merged.iterrows():
        if get_base_pay_only(row['Role']):
            continue
            
        role = str(row['Role']).lower()
        
        if 'bartender' in role:
            bartender_merchant_fee = row['Service Tips'] * 0.03
            final_pay = row['Estimated Total Pay'] + row['Service Tips'] - bartender_merchant_fee + row['Tip-Out Tips']
        else:
            final_pay = row['Estimated Total Pay'] + row['Tip-Out Tips']
        
        if 'server' in role and 'trainee' not in role:
            final_pay += row['Total Tips']
        
        merged.at[idx, 'Final Pay'] = final_pay
        
        if row['Total Hours Worked'] > 0:
            merged.at[idx, 'Effective Hourly Rate'] = final_pay / row['Total Hours Worked']

    numeric_cols = ['Gross Sales', 'Net Sales', 'Service Tips', 'Tip Out', 'Gross Tips', 'Merchant Fee', 
                    'Total Tips', 'Subtotal', 'Tip-Out Tips', 'Final Pay', 'Effective Hourly Rate',
                    'Raw Hours', 'No. of Breaks', 'Total Break Time']
    for col in numeric_cols:
        if col in merged.columns:
            merged[col] = merged[col].round(2)

    output_columns = [
        'Name', 'Role', 'Hourly Rate', 'Raw Hours', 'No. of Breaks', 'Total Break Time',
        'Total Hours Worked', 'Estimated Total Pay', 'Gross Sales', 'Net Sales', 'Service Tips',
        'Tip Out', 'Tip-Out Tips', 'Merchant Fee', 'Final Pay', 'Effective Hourly Rate'
    ]
    
    output_columns = [col for col in output_columns if col in merged.columns]
    
    role_order = [
        'Server', 'Bartender', 'Busser', 'Food-Bar Runner', 'Food Runner', 'Cashier/Host',
        'Food-Bar Prep', 'Prep Cook', 'Dish Washer', 'Host Trainee', 'Server Trainee', 'Busser Trainee', 'Manager'
    ]
    
    final_rows = []
    
    for role in role_order:
        role_mask = merged['Role'].str.lower() == role.lower()
        role_data = merged[role_mask].copy()
        
        if not role_data.empty:
            role_data = role_data.sort_values('Name')
            final_rows.append(role_data[output_columns])
            spacer = pd.DataFrame([[''] * len(output_columns)], columns=output_columns)
            final_rows.append(spacer)
    
    if final_rows:
        final_output = pd.concat(final_rows, ignore_index=True)
    else:
        final_output = pd.DataFrame(columns=output_columns)
    
    if len(final_output) > 0 and final_output.iloc[-1].isna().all():
        final_output = final_output.iloc[:-1]

    # Add totals
    employee_rows = final_output[
        ~final_output['Name'].astype(str).str.contains('TOTAL|GRAND TOTAL', na=False, case=False) &
        (final_output['Name'].astype(str).str.strip() != '')
    ].copy()
    employee_rows = employee_rows.reset_index(drop=True)
    
    roles_in_order = []
    for role in employee_rows['Role']:
        if role not in roles_in_order:
            roles_in_order.append(role)
    
    output_rows = []
    
    for i, role in enumerate(roles_in_order):
        role_rows = employee_rows[employee_rows['Role'] == role]
        
        for _, row in role_rows.iterrows():
            output_rows.append(row.to_dict())
        
        if role == 'Server':
            server_rows = employee_rows[employee_rows['Role'] == 'Server']
            total_row = {col: '' for col in employee_rows.columns}
            total_row['Name'] = 'TOTAL'
            total_row['Role'] = 'Server'
            
            sum_cols = ['Raw Hours', 'Total Break Time', 'Total Hours Worked', 'Estimated Total Pay', 
                       'Gross Sales', 'Net Sales', 'Service Tips', 'Tip Out', 'Tip-Out Tips', 'Merchant Fee', 'Final Pay']
            
            for col in sum_cols:
                if col in server_rows.columns:
                    values = pd.to_numeric(server_rows[col], errors='coerce').fillna(0)
                    total_row[col] = round(values.sum(), 2)
            
            total_hours = total_row['Total Hours Worked']
            total_final_pay = total_row['Final Pay']
            if total_hours > 0:
                total_row['Effective Hourly Rate'] = round(total_final_pay / total_hours, 2)
            else:
                total_row['Effective Hourly Rate'] = 0
            
            output_rows.append(total_row)
        
        if i < len(roles_in_order) - 1:
            spacer_row = {col: '' for col in employee_rows.columns}
            output_rows.append(spacer_row)
    
    final_output = pd.DataFrame(output_rows)
    
    grand_total_row = {col: '' for col in employee_rows.columns}
    grand_total_row['Name'] = 'GRAND TOTAL'
    grand_total_row['Role'] = 'All Sections'
    
    sum_cols = ['Raw Hours', 'Total Break Time', 'Total Hours Worked', 'Estimated Total Pay', 
               'Gross Sales', 'Net Sales', 'Service Tips', 'Tip Out', 'Tip-Out Tips', 'Merchant Fee', 'Final Pay']
    
    for col in sum_cols:
        if col in employee_rows.columns:
            values = pd.to_numeric(employee_rows[col], errors='coerce').fillna(0)
            grand_total_row[col] = round(values.sum(), 2)
    
    total_hours = grand_total_row['Total Hours Worked']
    total_final_pay = grand_total_row['Final Pay']
    if total_hours > 0:
        grand_total_row['Effective Hourly Rate'] = round(total_final_pay / total_hours, 2)
    else:
        grand_total_row['Effective Hourly Rate'] = 0
    
    blank_row = {col: '' for col in employee_rows.columns}
    final_output = pd.concat([final_output, pd.DataFrame([blank_row])], ignore_index=True)
    final_output = pd.concat([final_output, pd.DataFrame([grand_total_row])], ignore_index=True)

    # Create a date range row to insert at the top (before headers)
    # This creates a row with the date range in column A, and empty in other columns
    date_row_data = {col: '' for col in final_output.columns}
    date_row_data[output_columns[0]] = date_range_text  # Put date in first column (Name)
    
    # Insert the date row at the beginning
    final_output = pd.concat([pd.DataFrame([date_row_data]), final_output], ignore_index=True)

    downloads_dir = Path.home() / "Downloads"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    
    excel_path = downloads_dir / f"processed_payroll_{timestamp}.xlsx"
    
    # Write to Excel with black text (default is black, no styling needed)
    with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
        final_output.to_excel(writer, sheet_name='Payroll', index=False)
        # Get the workbook and worksheet
        workbook = writer.book
        worksheet = writer.sheets['Payroll']
        
        # Set all text to black (removing any automatic coloring)
        from openpyxl.styles import Font
        black_font = Font(color='000000')
        
        for row in worksheet.iter_rows():
            for cell in row:
                cell.font = black_font
    
    csv_path = downloads_dir / f"processed_payroll_{timestamp}.csv"
    final_output.to_csv(csv_path, index=False)
    
    return str(excel_path)

# ============= GUI CODE =============

class PayrollApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Payroll Processor Pro")
        self.root.geometry("700x850")
        self.root.configure(bg="#f0f0f0")
        
        # Center the window on screen
        self.center_window()
        
        self.productivity_file = ""
        self.labor_file = ""
        self.timecard_file = ""

        self.tipout_var = tk.StringVar(value="7")

        self.role_vars = {
            "Busser": tk.StringVar(value="42"),
            "Food Runner": tk.StringVar(value="19"),
            "Food-Bar Runner": tk.StringVar(value="29"),
            "Food-Bar Prep": tk.StringVar(value="4.5"),
            "Cashier/Host": tk.StringVar(value="3"),
            "Bartender": tk.StringVar(value="2.5")
        }
        
        # Configure styles
        self.setup_styles()
        self.setup_ui()
    
    def center_window(self):
        """Center the window on the screen"""
        self.root.update_idletasks()
        width = 700
        height = 850
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')
    
    def setup_styles(self):
        """Configure custom colors and fonts"""
        self.colors = {
            'bg': '#f5f5f5',
            'primary': '#2c3e50',
            'secondary': '#3498db',
            'success': '#27ae60',
            'danger': '#e74c3c',
            'warning': '#f39c12',
            'white': '#ffffff',
            'gray': '#7f8c8d',
            'light_gray': '#ecf0f1'
        }
        
        self.fonts = {
            'title': ('Helvetica', 18, 'bold'),
            'heading': ('Helvetica', 12, 'bold'),
            'normal': ('Helvetica', 10),
            'button': ('Helvetica', 11, 'bold')
        }
    
    def create_card(self, parent, title, **kwargs):
        """Create a styled card frame"""
        card = tk.Frame(parent, bg=self.colors['white'], relief=tk.RAISED, bd=1)
        card.pack(fill="x", pady=10, padx=20, **kwargs)
        
        # Title bar
        title_bar = tk.Frame(card, bg=self.colors['primary'], height=35)
        title_bar.pack(fill="x")
        title_bar.pack_propagate(False)
        
        title_label = tk.Label(title_bar, text=title, font=self.fonts['heading'],
                               bg=self.colors['primary'], fg=self.colors['white'])
        title_label.pack(side="left", padx=15, pady=8)
        
        content = tk.Frame(card, bg=self.colors['white'], padx=15, pady=15)
        content.pack(fill="x")
        
        return content
    
    def setup_ui(self):
        """Setup the main UI"""
        # Main container
        main_container = tk.Frame(self.root, bg=self.colors['bg'])
        main_container.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Header
        header = tk.Frame(main_container, bg=self.colors['primary'], height=80)
        header.pack(fill="x", pady=(0, 20))
        header.pack_propagate(False)
        
        # App icon
        icon_label = tk.Label(header, text="💰", font=('Helvetica', 36),
                             bg=self.colors['primary'], fg=self.colors['white'])
        icon_label.pack(side="left", padx=20, pady=15)
        
        title_label = tk.Label(header, text="Payroll Processor Pro", 
                               font=self.fonts['title'],
                               bg=self.colors['primary'], fg=self.colors['white'])
        title_label.pack(side="left", padx=10)
        
        subtitle_label = tk.Label(header, text="Automated Payroll Processing System",
                                  font=('Helvetica', 10),
                                  bg=self.colors['primary'], fg=self.colors['light_gray'])
        subtitle_label.pack(side="left", padx=10, pady=(25, 0))
        
        # Files Card
        files_card = self.create_card(main_container, "📄 Input Files")
        
        # Productivity file row
        prod_frame = tk.Frame(files_card, bg=self.colors['white'])
        prod_frame.pack(fill="x", pady=8)
        tk.Label(prod_frame, text="Productivity CSV:", width=20, anchor="w",
                font=self.fonts['normal'], bg=self.colors['white']).pack(side="left")
        self.prod_display = tk.Label(prod_frame, text="No file selected", 
                                     bg=self.colors['white'], fg=self.colors['gray'],
                                     font=self.fonts['normal'], anchor="w")
        self.prod_display.pack(side="left", padx=10, fill="x", expand=True)
        tk.Button(prod_frame, text="Browse", command=self.select_productivity,
                 bg=self.colors['secondary'], fg='white', cursor="hand2",
                 relief=tk.FLAT, padx=15, pady=3).pack(side="right")
        
        # Labor file row
        labor_frame = tk.Frame(files_card, bg=self.colors['white'])
        labor_frame.pack(fill="x", pady=8)
        tk.Label(labor_frame, text="Labor CSV:", width=20, anchor="w",
                font=self.fonts['normal'], bg=self.colors['white']).pack(side="left")
        self.labor_display = tk.Label(labor_frame, text="No file selected",
                                      bg=self.colors['white'], fg=self.colors['gray'],
                                      font=self.fonts['normal'], anchor="w")
        self.labor_display.pack(side="left", padx=10, fill="x", expand=True)
        tk.Button(labor_frame, text="Browse", command=self.select_labor,
                 bg=self.colors['secondary'], fg='white', cursor="hand2",
                 relief=tk.FLAT, padx=15, pady=3).pack(side="right")
        
        # Timecard file row
        timecard_frame = tk.Frame(files_card, bg=self.colors['white'])
        timecard_frame.pack(fill="x", pady=8)
        tk.Label(timecard_frame, text="Timecard File:", width=20, anchor="w",
                font=self.fonts['normal'], bg=self.colors['white']).pack(side="left")
        self.time_display = tk.Label(timecard_frame, text="No file selected",
                                     bg=self.colors['white'], fg=self.colors['gray'],
                                     font=self.fonts['normal'], anchor="w")
        self.time_display.pack(side="left", padx=10, fill="x", expand=True)
        tk.Button(timecard_frame, text="Browse", command=self.select_timecard,
                 bg=self.colors['secondary'], fg='white', cursor="hand2",
                 relief=tk.FLAT, padx=15, pady=3).pack(side="right")

        # Tip-Out Settings Card
        settings_card = self.create_card(main_container, "💵 Tip-Out Settings")

        tipout_frame = tk.Frame(settings_card, bg=self.colors['white'])
        tipout_frame.pack(fill="x", pady=5)

        tk.Label(tipout_frame, text="Server Tip-Out %", width=20, anchor="w",
                 bg=self.colors['white']).pack(side="left")

        tk.Entry(tipout_frame, textvariable=self.tipout_var, width=10).pack(side="left")

        tk.Label(tipout_frame,
                 bg=self.colors['white']).pack(side="left", padx=5)

        tk.Label(settings_card, text="Role Pool Percentages",
                 font=self.fonts['heading'],
                 bg=self.colors['white']).pack(anchor="w", pady=(10, 5))

        for role, var in self.role_vars.items():
            row = tk.Frame(settings_card, bg=self.colors['white'])
            row.pack(fill="x", pady=2)

            tk.Label(row, text=role, width=20, anchor="w",
                     bg=self.colors['white']).pack(side="left")

            tk.Entry(row, textvariable=var, width=10).pack(side="left")

            tk.Label(row, text="%", bg=self.colors['white']).pack(side="left")
        
        # Status Card
        status_card = self.create_card(main_container, "⚡ Processing Status")
        
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = tk.Label(status_card, textvariable=self.status_var,
                                     font=self.fonts['normal'], bg=self.colors['white'],
                                     fg=self.colors['success'])
        self.status_label.pack(pady=5)
        
        # Progress bar
        self.progress = ttk.Progressbar(status_card, mode='indeterminate', length=400)
        self.progress.pack(pady=10)
        
        # Process Button
        button_frame = tk.Frame(main_container, bg=self.colors['bg'])
        button_frame.pack(pady=20)
        
        self.process_btn = tk.Button(button_frame, text="▶ PROCESS PAYROLL", 
                                     command=self.process_payroll,
                                     font=self.fonts['button'],
                                     bg=self.colors['secondary'], fg=self.colors['white'],
                                     padx=30, pady=12, cursor="hand2",
                                     relief=tk.RAISED, bd=2)
        self.process_btn.pack()
        
        # Footer
        footer = tk.Frame(main_container, bg=self.colors['bg'])
        footer.pack(fill="x", pady=(20, 0))
        tk.Label(footer, text="© 2025 Payroll Processor Pro | Version 1.0",
                font=('Helvetica', 8), bg=self.colors['bg'], fg=self.colors['gray']).pack()
    
    def select_productivity(self):
        self.productivity_file = filedialog.askopenfilename(
            title="Select Productivity CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if self.productivity_file:
            self.prod_display.config(text=Path(self.productivity_file).name, fg=self.colors['primary'])
            self.check_ready()
    
    def select_labor(self):
        self.labor_file = filedialog.askopenfilename(
            title="Select Labor CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if self.labor_file:
            self.labor_display.config(text=Path(self.labor_file).name, fg=self.colors['primary'])
            self.check_ready()
    
    def select_timecard(self):
        self.timecard_file = filedialog.askopenfilename(
            title="Select Timecard File",
            filetypes=[("CSV files", "*.csv"), ("Excel files", "*.xlsx"), ("All files", "*.*")]
        )
        if self.timecard_file:
            self.time_display.config(text=Path(self.timecard_file).name, fg=self.colors['primary'])
            self.check_ready()
    
    def check_ready(self):
        if self.productivity_file and self.labor_file and self.timecard_file:
            self.process_btn.config(bg=self.colors['success'], state="normal")
            self.status_var.set("All files selected. Ready to process!")
            self.status_label.config(fg=self.colors['success'])
        else:
            self.process_btn.config(bg=self.colors['secondary'], state="normal")
            self.status_var.set("Please select all three files")
            self.status_label.config(fg=self.colors['gray'])
    
    def process_payroll(self):
        if not all([self.productivity_file, self.labor_file, self.timecard_file]):
            messagebox.showwarning("Missing Files", "Please select all three files before processing.")
            return
        
        self.process_btn.config(state="disabled", bg=self.colors['gray'])
        self.status_var.set("Processing payroll... Please wait")
        self.status_label.config(fg=self.colors['warning'])
        self.progress.start()
        
        thread = threading.Thread(target=self.run_payroll)
        thread.start()
    
    def run_payroll(self):
        try:
            percentages = {}

            for role, var in self.role_vars.items():
                percentages[role] = float(var.get()) / 100

            tipout_percentage = float(self.tipout_var.get()) / 100

            output_file = process_payroll(
                self.productivity_file,
                self.labor_file,
                self.timecard_file,
                percentages=percentages,
                tipout_percentage=tipout_percentage
            )

            self.root.after(0, self.on_success, output_file)

        except Exception as e:
            self.root.after(0, self.on_error, str(e))
    
    def on_success(self, output_file):
        self.progress.stop()
        self.status_var.set("Complete! Output saved to Downloads folder")
        self.status_label.config(fg=self.colors['success'])
        self.process_btn.config(state="normal", bg=self.colors['success'])
        
        result = messagebox.askyesno("✅ Success", 
            f"Payroll processed successfully!\n\n📄 Output saved to:\n{output_file}\n\n📂 Open folder?")
        if result:
            os.startfile(Path(output_file).parent)
    
    def on_error(self, error_msg):
        self.progress.stop()
        self.status_var.set(f"Error: {error_msg[:50]}...")
        self.status_label.config(fg=self.colors['danger'])
        self.process_btn.config(state="normal", bg=self.colors['secondary'])
        messagebox.showerror("❌ Processing Error", f"An error occurred:\n\n{error_msg}")

if __name__ == "__main__":
    root = tk.Tk()
    app = PayrollApp(root)
    root.mainloop()