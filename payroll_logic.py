import pandas as pd
from pathlib import Path
from datetime import datetime
import re

ROLE_PERCENTAGES = {
    "Busser": 0.42,
    "Food Runner": 0.19,
    "Food-Bar Runner": 0.29,
    "Food-Bar Prep": 0.045,
    "Cashier/Host": 0.03,
    "Bartender": 0.025
}

# -----------------------------
# UTILITY FUNCTIONS
# -----------------------------

def clean_money(value):
    """Convert currency strings to float values"""
    if pd.isna(value):
        return 0.0
    
    value = str(value)
    value = value.replace('$', '')
    value = value.replace(',', '')
    value = value.replace('%', '')
    value = value.strip()
    
    try:
        return float(value)
    except:
        return 0.0


def get_last_initial(name):
    """Extract first name and last initial for matching across files"""
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
    """Create a normalized name column for merging"""
    df['Name_Match'] = df[name_column].apply(get_last_initial)
    return df


def get_base_pay_only(role):
    """Determine if role gets only base hourly pay (no tips)"""
    role_lower = str(role).lower()
    base_only_roles = ['server trainee', 'host trainee', 'busser trainee', 
                       'dish washer', 'prep cook']
    
    for base_role in base_only_roles:
        if base_role in role_lower:
            return True
    return False

def calculate_breaks_from_timecard(timecard_df):
    # Clean column names
    timecard_df.columns = [c.strip() for c in timecard_df.columns]
    
    # Ensure required columns exist
    required_cols = ['Name', 'Role', 'Clock In', 'Clock Out']
    for col in required_cols:
        if col not in timecard_df.columns:
            raise Exception(f'Missing required column in Time Card file: {col}')
    
    # Convert time columns to datetime for calculations
    timecard_df['Clock In'] = pd.to_datetime(timecard_df['Clock In'], format='%I:%M %p', errors='coerce')
    timecard_df['Clock Out'] = pd.to_datetime(timecard_df['Clock Out'], format='%I:%M %p', errors='coerce')
    
    # Ensure Total Hours Worked column exists
    if 'Total Hours Worked (h)' not in timecard_df.columns:
        timecard_df['Total Hours Worked (h)'] = (timecard_df['Clock Out'] - timecard_df['Clock In']).dt.total_seconds() / 3600
        timecard_df['Total Hours Worked (h)'] = timecard_df['Total Hours Worked (h)'].fillna(0)
    
    # Check for Unpaid Break column
    has_unpaid_break = 'Unpaid Break (h)' in timecard_df.columns
    
    # Initialize break tracking columns
    timecard_df['Break Minutes'] = 0.0
    timecard_df['Break Count'] = 0
    timecard_df['Break Hours'] = 0.0
    timecard_df['Break Already Subtracted'] = False
    
    for idx, row in timecard_df.iterrows():
        clock_in = row['Clock In']
        clock_out = row['Clock Out']
        
        if pd.isna(clock_in) or pd.isna(clock_out):
            continue
        
        # Check if Unpaid Break column has a non-zero value
        if has_unpaid_break and pd.notna(row.get('Unpaid Break (h)')) and row['Unpaid Break (h)'] != '':
            try:
                break_hours = float(row['Unpaid Break (h)'])
                if break_hours > 0:
                    break_minutes = break_hours * 60
                    timecard_df.at[idx, 'Break Minutes'] = break_minutes
                    timecard_df.at[idx, 'Break Count'] = 1
                    timecard_df.at[idx, 'Break Hours'] = break_hours
                    timecard_df.at[idx, 'Break Already Subtracted'] = True
                    continue
            except:
                pass
        
        # No unpaid break recorded - calculate break time to subtract
        clock_in_hour = clock_in.hour + clock_in.minute / 60
        
        # Check if working a double shift (clocked in before 1pm AND out after 9pm)
        if clock_in_hour < 13 and clock_out.hour >= 21:
            timecard_df.at[idx, 'Break Minutes'] = 40.0
            timecard_df.at[idx, 'Break Count'] = 2
            timecard_df.at[idx, 'Break Hours'] = 40.0 / 60.0
        else:
            timecard_df.at[idx, 'Break Minutes'] = 20.0
            timecard_df.at[idx, 'Break Count'] = 1
            timecard_df.at[idx, 'Break Hours'] = 20.0 / 60.0
        
        timecard_df.at[idx, 'Break Already Subtracted'] = False
    
    # Calculate adjusted hours
    timecard_df['Adjusted Hours'] = timecard_df.apply(
        lambda row: row['Total Hours Worked (h)'] if row['Break Already Subtracted']
        else max(0.0, float(row['Total Hours Worked (h)']) - float(row['Break Hours'])),
        axis=1
    )
    
    # Convert columns to proper types before aggregation
    timecard_df['Total Hours Worked (h)'] = timecard_df['Total Hours Worked (h)'].astype(float)
    timecard_df['Break Count'] = timecard_df['Break Count'].astype(int)
    timecard_df['Break Hours'] = timecard_df['Break Hours'].astype(float)
    timecard_df['Adjusted Hours'] = timecard_df['Adjusted Hours'].astype(float)
    
    # Aggregate by Name AND Role together
    timecard_agg = timecard_df.groupby(['Name', 'Role'], as_index=False).agg({
        'Total Hours Worked (h)': 'sum',
        'Break Count': 'sum',
        'Break Hours': 'sum',
        'Adjusted Hours': 'sum'
    })
    
    # Rename columns
    timecard_agg.columns = ['Name', 'Role', 'Raw Hours', 'No. of Breaks', 'Total Break Time', 'Total Hours Worked']
    
    # Convert No. of Breaks to int
    timecard_agg['No. of Breaks'] = timecard_agg['No. of Breaks'].astype(int)
    
    # Round everything to 2 decimal places
    timecard_agg['Total Break Time'] = timecard_agg['Total Break Time'].round(2)
    timecard_agg['Total Hours Worked'] = timecard_agg['Total Hours Worked'].round(2)
    timecard_agg['Raw Hours'] = timecard_agg['Raw Hours'].round(2)
    
    return timecard_agg

# -----------------------------
# MAIN PAYROLL FUNCTION
# -----------------------------

def process_payroll(
    productivity_file,
    labor_file,
    timecard_file,
    percentages=None
):
    if percentages is None:
        percentages = ROLE_PERCENTAGES

    # ---------------------------------
    # READ INPUT FILES
    # ---------------------------------
    productivity_df = pd.read_csv(productivity_file, skiprows=1)
    
    # Read time card file (required)
    if timecard_file.endswith('.xlsx'):
        timecard_df = pd.read_excel(timecard_file, skiprows=1)
    else:
        timecard_df = pd.read_csv(timecard_file, skiprows=1)
    
    # Read labor file for hourly rates only
    if labor_file.endswith('.xlsx'):
        labor_df = pd.read_excel(labor_file, skiprows=1)
    else:
        labor_df = pd.read_csv(labor_file, skiprows=1)

    # ---------------------------------
    # CLEAN COLUMN NAMES
    # ---------------------------------
    productivity_df.columns = [c.strip() for c in productivity_df.columns]
    labor_df.columns = [c.strip() for c in labor_df.columns]
    
    labor_df.columns = (
        labor_df.columns
        .str.strip()
        .str.replace(r"\s*\(.*?\)", "", regex=True)
        .str.replace(r"\s*\(h\)", "", regex=True)
    )

    # ---------------------------------
    # VALIDATE REQUIRED COLUMNS
    # ---------------------------------
    productivity_required = ['Name', 'Role', 'Gross Sales', 'Service Tips']
    labor_required = ['Name', 'Role', 'Hourly Rate']
    
    for col in productivity_required:
        if col not in productivity_df.columns:
            raise Exception(f'Missing column in Productivity CSV: {col}')
    
    for col in labor_required:
        if col not in labor_df.columns:
            raise Exception(f'Missing column in Labor file: {col}')

    # ---------------------------------
    # CLEAN DATA VALUES
    # ---------------------------------
    productivity_df['Gross Sales'] = productivity_df['Gross Sales'].apply(clean_money)
    if 'Net Sales' in productivity_df.columns:
        productivity_df['Net Sales'] = productivity_df['Net Sales'].apply(clean_money)
    else:
        productivity_df['Net Sales'] = productivity_df['Gross Sales']
    
    productivity_df['Service Tips'] = productivity_df['Service Tips'].apply(clean_money)
    
    labor_df['Hourly Rate'] = labor_df['Hourly Rate'].apply(clean_money)
    
    # ---------------------------------
    # CALCULATE BREAKS FROM TIMECARD
    # ---------------------------------
    timecard_agg = calculate_breaks_from_timecard(timecard_df)
    
    # Drop any duplicate (Name, Role) combinations
    timecard_agg = timecard_agg.drop_duplicates(subset=['Name', 'Role'], keep='first')
    
    # ---------------------------------
    # MERGE FILES USING NAME AND ROLE MATCHING
    # ---------------------------------
    # Create normalized name columns for matching
    timecard_agg = normalize_name_for_matching(timecard_agg, 'Name')
    productivity_df = normalize_name_for_matching(productivity_df, 'Name')
    labor_df = normalize_name_for_matching(labor_df, 'Name')
    
    timecard_agg['Original_Name'] = timecard_agg['Name']
    
    # Merge productivity data into timecard
    merged = pd.merge(
        timecard_agg,
        productivity_df[['Name_Match', 'Role', 'Gross Sales', 'Net Sales', 'Service Tips']],
        on=['Name_Match', 'Role'],
        how='left',
        suffixes=('', '_prod')
    )
    
    # Merge hourly rate from labor file
    merged = pd.merge(
        merged,
        labor_df[['Name_Match', 'Role', 'Hourly Rate']],
        on=['Name_Match', 'Role'],
        how='left',
        suffixes=('', '_labor')
    )
    
    # Restore original name
    merged['Name'] = merged['Original_Name']
    
    # Fill missing values
    merged['Gross Sales'] = merged['Gross Sales'].fillna(0)
    merged['Net Sales'] = merged['Net Sales'].fillna(0)
    merged['Service Tips'] = merged['Service Tips'].fillna(0)
    merged['Hourly Rate'] = merged['Hourly Rate'].fillna(0)
    
    # Drop any duplicate rows
    merged = merged.drop_duplicates(subset=['Name', 'Role'], keep='first')
    
    # Calculate Estimated Total Pay
    merged['Estimated Total Pay'] = merged['Total Hours Worked'] * merged['Hourly Rate']
    merged['Estimated Total Pay'] = merged['Estimated Total Pay'].round(2)
    
    # ---------------------------------
    # INITIALIZE CALCULATION COLUMNS
    # ---------------------------------
    merged['Tip Out'] = 0.0
    merged['Gross Tips'] = 0.0
    merged['Merchant Fee'] = 0.0
    merged['Total Tips'] = 0.0
    merged['Subtotal'] = 0.0
    merged['Tip-Out Tips'] = 0.0
    merged['Final Pay'] = 0.0
    merged['Effective Hourly Rate'] = 0.0

    # ---------------------------------
    # CALCULATE BASE PAY FOR NON-TIP ROLES
    # ---------------------------------
    for idx, row in merged.iterrows():
        if get_base_pay_only(row['Role']):
            merged.at[idx, 'Final Pay'] = row['Estimated Total Pay']
            merged.at[idx, 'Effective Hourly Rate'] = row['Estimated Total Pay'] / row['Total Hours Worked'] if row['Total Hours Worked'] > 0 else 0

            # Zero out tip-related fields only
            merged.at[idx, 'Tip Out'] = 0
            merged.at[idx, 'Service Tips'] = 0
            merged.at[idx, 'Merchant Fee'] = 0

    # ---------------------------------
    # CALCULATE TIP CONTRIBUTIONS FOR SERVERS
    # ---------------------------------
    server_mask = merged['Role'].str.contains('Server', case=False, na=False) & \
                  ~merged['Role'].str.contains('Trainee', case=False, na=False)
    
    merged.loc[server_mask, 'Tip Out'] = merged.loc[server_mask, 'Gross Sales'] * 0.07
    merged.loc[server_mask, 'Gross Tips'] = merged.loc[server_mask, 'Service Tips'] - merged.loc[server_mask, 'Tip Out']
    merged.loc[server_mask, 'Merchant Fee'] = merged.loc[server_mask, 'Gross Tips'].apply(lambda x: max(0, x * 0.03))
    merged.loc[server_mask, 'Total Tips'] = merged.loc[server_mask, 'Gross Tips'] - merged.loc[server_mask, 'Merchant Fee']
    merged.loc[server_mask, 'Subtotal'] = merged.loc[server_mask, 'Estimated Total Pay'] + merged.loc[server_mask, 'Total Tips']
    
    total_tip_out = merged['Tip Out'].sum()
    total_merchant_fee = merged['Merchant Fee'].sum()
    total_pool = total_tip_out - total_merchant_fee

    # ---------------------------------
    # DISTRIBUTE TIP POOL BY ROLE
    # ---------------------------------
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

    # ---------------------------------
    # CALCULATE INDIVIDUAL TIP OUTS
    # ---------------------------------
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

    # ---------------------------------
    # CALCULATE FINAL PAY
    # ---------------------------------
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

    # ---------------------------------
    # ROUND NUMERIC VALUES
    # ---------------------------------
    numeric_cols = ['Gross Sales', 'Net Sales', 'Service Tips', 'Tip Out', 'Gross Tips', 'Merchant Fee', 
                    'Total Tips', 'Subtotal', 'Tip-Out Tips', 'Final Pay', 'Effective Hourly Rate',
                    'Raw Hours', 'No. of Breaks', 'Total Break Time']
    for col in numeric_cols:
        if col in merged.columns:
            merged[col] = merged[col].round(2)

    # ---------------------------------
    # FILTER AND SORT OUTPUT COLUMNS
    # ---------------------------------
    output_columns = [
        'Name',
        'Role',
        'Hourly Rate',
        'Raw Hours',
        'No. of Breaks',
        'Total Break Time',
        'Total Hours Worked',
        'Estimated Total Pay',
        'Gross Sales',
        'Net Sales',
        'Service Tips',
        'Tip Out',
        'Tip-Out Tips',
        'Merchant Fee',
        'Final Pay',
        'Effective Hourly Rate'
    ]
    
    output_columns = [col for col in output_columns if col in merged.columns]
    
    # Create final output with sorted roles and row spaces
    role_order = [
        'Server',
        'Bartender',
        'Busser',
        'Food-Bar Runner',
        'Food Runner',
        'Cashier/Host',
        'Food-Bar Prep',
        'Prep Cook',
        'Dish Washer',
        'Host Trainee',
        'Server Trainee',
        'Busser Trainee',
        'Manager'
    ]
    
    final_rows = []
    
    for role in role_order:
        # Match role (case insensitive)
        role_mask = merged['Role'].str.lower() == role.lower()
        role_data = merged[role_mask].copy()
        
        if not role_data.empty:
            # Sort by name within role
            role_data = role_data.sort_values('Name')
            final_rows.append(role_data[output_columns])
            # Add empty row as spacer
            spacer = pd.DataFrame([[''] * len(output_columns)], columns=output_columns)
            final_rows.append(spacer)
    
    # Combine all sections
    if final_rows:
        final_output = pd.concat(final_rows, ignore_index=True)
    else:
        final_output = pd.DataFrame(columns=output_columns)
    
    # Remove last spacer row if it exists
    if len(final_output) > 0 and final_output.iloc[-1].isna().all():
        final_output = final_output.iloc[:-1]

    # ---------------------------------
    # ADD TOTAL ROWS (SERVER TOTAL + GRAND TOTAL ONLY) - CLEAN VERSION
    # ---------------------------------
    
    # Start with ONLY employee rows (no totals, no spacer rows)
    employee_rows = final_output[
        ~final_output['Name'].astype(str).str.contains('TOTAL|GRAND TOTAL', na=False, case=False) &
        (final_output['Name'].astype(str).str.strip() != '')
    ].copy()
    employee_rows = employee_rows.reset_index(drop=True)
    
    # Identify unique roles in order of appearance
    roles_in_order = []
    for role in employee_rows['Role']:
        if role not in roles_in_order:
            roles_in_order.append(role)
    
    # Rebuild output from scratch with clean spacing
    output_rows = []
    
    for i, role in enumerate(roles_in_order):
        role_rows = employee_rows[employee_rows['Role'] == role]
        
        # Add all employees in this role
        for _, row in role_rows.iterrows():
            output_rows.append(row.to_dict())
        
        # Add Server TOTAL row immediately after the last Server employee (no blank row after it)
        if role == 'Server':
            server_rows = employee_rows[employee_rows['Role'] == 'Server']
            total_row = {col: '' for col in employee_rows.columns}
            total_row['Name'] = 'TOTAL'
            total_row['Role'] = 'Server'
            
            sum_cols = ['Raw Hours', 'Total Break Time', 'Total Hours Worked', 
                        'Estimated Total Pay', 'Gross Sales', 'Net Sales', 
                        'Service Tips', 'Tip Out', 'Tip-Out Tips', 'Merchant Fee', 'Final Pay']
            
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
        
        # Add ONE blank row separator after each role (except the last role)
        if i < len(roles_in_order) - 1:
            spacer_row = {col: '' for col in employee_rows.columns}
            output_rows.append(spacer_row)
    
    # Create clean final_output
    final_output = pd.DataFrame(output_rows)
    
    # Calculate GRAND TOTAL from employee rows only (exclude Server TOTAL row)
    grand_total_row = {col: '' for col in employee_rows.columns}
    grand_total_row['Name'] = 'GRAND TOTAL'
    grand_total_row['Role'] = 'All Sections'
    
    sum_cols = ['Raw Hours', 'Total Break Time', 'Total Hours Worked', 
                'Estimated Total Pay', 'Gross Sales', 'Net Sales', 
                'Service Tips', 'Tip Out', 'Tip-Out Tips', 'Merchant Fee', 'Final Pay']
    
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
    
    # Add a blank row before GRAND TOTAL, then the GRAND TOTAL row
    blank_row = {col: '' for col in employee_rows.columns}
    final_output = pd.concat([final_output, pd.DataFrame([blank_row])], ignore_index=True)
    final_output = pd.concat([final_output, pd.DataFrame([grand_total_row])], ignore_index=True)

    # ---------------------------------
    # EXPORT RESULTS
    # ---------------------------------
    downloads_dir = Path.home() / "Downloads"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    
    excel_path = downloads_dir / f"processed_payroll_{timestamp}.xlsx"
    final_output.to_excel(excel_path, index=False)
    
    csv_path = downloads_dir / f"processed_payroll_{timestamp}.csv"
    final_output.to_csv(csv_path, index=False)
    
    return str(excel_path)