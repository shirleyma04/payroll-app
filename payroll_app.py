"""
Payroll Processing Application - Tkinter Desktop GUI
Automatically downloads to Downloads folder when processing
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import pandas as pd
import numpy as np
from datetime import datetime
import os
import threading
import traceback
import re

class PayrollProcessor:
    """Payroll processing engine"""
    
    def __init__(self, mode="MAAX"):
        self.mode = mode
        self.labor_data = None
        self.productivity_data = None
        self.timecard_data = None
        self.processed_data = None
        self.date_range = None
        
        if mode == "MAAX":
            self.default_percentages = {
                "Busser": 0.42,
                "Food Runner": 0.19,
                "Food-Bar Runner": 0.29,
                "Food-Bar Prep": 0.045,
                "Cashier/Host": 0.03,
                "Bartender": 0.025
            }
            self.tipout_percentage = 0.07  # MAAX tipout is 7%
        else:  # Tomahawk
            self.default_percentages = {
                "Busser": 0.42,
                "Cashier/Host": 0.03,
                "Bartender": 0.025
            }
            self.tipout_percentage = 0.05  # Tomahawk tipout is 5%
        
        self.percentages = self.default_percentages.copy()
        
        # Define role order for output
        self.role_order = [
            "Server",
            "Server Trainee",
            "Bartender",
            "Busser",
            "Busser Trainee",
            "Food-Bar Runner",
            "Food Runner",
            "Cashier/Host",
            "Host Trainee",
            "Food-Bar Prep",
            "Prep Cook",
            "Dish Washer"
        ]

    def normalize_name(self, name):
        """Normalize name for matching - first name and first letter of last name"""
        if pd.isna(name) or name == '':
            return ''
        name_parts = str(name).strip().split()
        if len(name_parts) >= 2:
            # Return first name + first letter of last name
            return f"{name_parts[0]}_{name_parts[1][0]}"
        elif len(name_parts) == 1:
            return name_parts[0]
        return str(name).strip()

    def match_employee_data(self, labor_df, productivity_df, timecard_df):
        """
        Match employees across dataframes using normalized names (first name + first letter of last name)
        """
        # Create normalized name column for each dataframe
        labor_df['Normalized_Name'] = labor_df['Name'].apply(self.normalize_name)
        productivity_df['Normalized_Name'] = productivity_df['Name'].apply(self.normalize_name)
        timecard_df['Normalized_Name'] = timecard_df['Name'].apply(self.normalize_name)
        
        # For labor data, we need to keep all rows (including duplicates for same person with different roles)
        # We'll match based on normalized name and role
        labor_df['Match_Key'] = labor_df['Normalized_Name'] + '_' + labor_df['Role'].astype(str)
        productivity_df['Match_Key'] = productivity_df['Normalized_Name'] + '_' + productivity_df['Role'].astype(str)
        timecard_df['Match_Key'] = timecard_df['Normalized_Name'] + '_' + timecard_df['Role'].astype(str)
        
        return labor_df, productivity_df, timecard_df

    def load_labor_data(self, filepath):
        try:
            # Read the first row to get date range
            with open(filepath, 'r') as f:
                first_line = f.readline()
                # Extract date range
                date_match = re.search(r'Date Range:\s*(.+?)(?=,,,,|$)', first_line)
                if date_match:
                    self.date_range = date_match.group(1).strip()
                else:
                    self.date_range = ""
            
            df = pd.read_csv(filepath, skiprows=1)
            self.labor_data = df
            return True
        except Exception as e:
            raise Exception(f"Error loading labor data: {str(e)}")

    def load_productivity_data(self, filepath):
        try:
            df = pd.read_csv(filepath, skiprows=1)
            self.productivity_data = df
            return True
        except Exception as e:
            raise Exception(f"Error loading productivity data: {str(e)}")

    def load_timecard_data(self, filepath):
        try:
            df = pd.read_csv(filepath, skiprows=1)
            self.timecard_data = df
            return True
        except Exception as e:
            raise Exception(f"Error loading timecard data: {str(e)}")

    def calculate_breaks(self, timecard_data):
        """
        Calculate break information based on the specified logic.
        Goes through each row of the Time Card file and applies break logic.
        Aggregates break information per person (name + role).
        
        For each row:
        1. If Time Card already has a value in 'Unpaid Break (h)', count the break time and count as a break
        2. Else if Clock In < 1:00PM AND Clock Out > 9:00PM, subtract 1.0 hours (double shift) and count as 1 break
        3. Else subtract 0.5 hours (single shift) and count as 1 break
        4. If total hours < 3 hours, don't subtract any break and don't count it
        """
        break_summary = {}
        
        # Iterate through each row of the timecard
        for index, row in timecard_data.iterrows():
            name = row['Name']
            role = row['Role']
            key = f"{name}_{role}"
            
            # Initialize the summary for this person if not exists
            if key not in break_summary:
                break_summary[key] = {
                    'Name': name,
                    'Role': role,
                    'break_count': 0,  # Counts ALL breaks (from timecard + program added)
                    'total_break_minutes': 0,
                    'total_break_hours': 0,
                    'has_unpaid_break': False,
                    'days_without_break': 0,
                    'days_with_break': 0  # Track days with breaks in timecard
                }
            
            # Check if Unpaid Break (h) already has a value
            has_unpaid_break = False
            unpaid_break_hours = 0
            if 'Unpaid Break (h)' in row:
                unpaid_break_value = row.get('Unpaid Break (h)', '')
                # Check if it has a valid value (not NaN and not empty)
                if pd.notna(unpaid_break_value) and str(unpaid_break_value).strip() != '':
                    has_unpaid_break = True
                    break_summary[key]['has_unpaid_break'] = True
                    
                    # Get the break time from the timecard
                    try:
                        unpaid_break_hours = float(unpaid_break_value)
                    except:
                        unpaid_break_hours = 0
                    
                    # Count this as a break from the timecard
                    break_summary[key]['break_count'] += 1
                    break_summary[key]['days_with_break'] += 1
                    
                    # Add the break time from the timecard to total break time
                    break_summary[key]['total_break_minutes'] += unpaid_break_hours * 60
                    break_summary[key]['total_break_hours'] += unpaid_break_hours
            
            # Calculate total hours worked from the timecard
            clock_in = pd.to_datetime(row['Clock In'])
            clock_out = pd.to_datetime(row['Clock Out'])
            total_hours = (clock_out - clock_in).total_seconds() / 3600
            
            # Determine break minutes for subtraction (only if no unpaid break)
            if has_unpaid_break:
                # If Unpaid Break (h) exists, no additional subtraction needed
                # The break time is already counted above
                break_minutes = 0
            else:
                # No Unpaid Break (h) - apply the logic for subtraction
                if total_hours < 3:
                    # Less than 3 hours, no break
                    break_minutes = 0
                else:
                    # Get hours in decimal format (hours + minutes/60)
                    clock_in_hour = clock_in.hour + clock_in.minute / 60.0
                    clock_out_hour = clock_out.hour + clock_out.minute / 60.0
                    
                    # Check if it's a double shift (clock in before 1PM, clock out after 9PM)
                    if clock_in_hour < 13.0 and clock_out_hour > 21.0:
                        # Double shift: subtract 1.0 hours (60 minutes)
                        break_minutes = 60
                    else:
                        # Single shift: subtract 0.5 hours (30 minutes)
                        break_minutes = 30
                    
                    # Count this as a break added by the program
                    break_summary[key]['break_count'] += 1
                    break_summary[key]['days_without_break'] += 1
                    
                    # Add the program-added break time to total break time
                    break_hours = break_minutes / 60
                    break_summary[key]['total_break_minutes'] += break_minutes
                    break_summary[key]['total_break_hours'] += break_hours
        
        # Create summary dataframe
        summary_list = []
        for key, value in break_summary.items():
            summary_list.append({
                'Name': value['Name'],
                'Role': value['Role'],
                'break_count': value['break_count'],  # Total breaks (timecard + program added)
                'total_break_minutes': round(value['total_break_minutes'], 2),
                'total_break_hours': round(value['total_break_hours'], 2),
                'days_without_break': value['days_without_break'],
                'days_with_break': value['days_with_break'],
                'has_unpaid_break': value['has_unpaid_break']
            })
        
        return pd.DataFrame(summary_list)

    def process_payroll(self):
        if self.labor_data is None or self.productivity_data is None or self.timecard_data is None:
            raise Exception("Please load all required files first.")
        
        try:
            # Match employee data across files
            labor_df, productivity_df, timecard_df = self.match_employee_data(
                self.labor_data.copy(), 
                self.productivity_data.copy(), 
                self.timecard_data.copy()
            )
            
            break_df = self.calculate_breaks(timecard_df)
            
            # Create normalized name for break_df
            break_df['Normalized_Name'] = break_df['Name'].apply(self.normalize_name)
            break_df['Match_Key'] = break_df['Normalized_Name'] + '_' + break_df['Role'].astype(str)
            
            # Clean numeric columns
            labor_df['Hourly Rate'] = pd.to_numeric(
                labor_df['Hourly Rate'].astype(str).str.replace('$', '').str.replace(',', '').str.strip(),
                errors='coerce'
            )
            labor_df['Regular Hours (h)'] = pd.to_numeric(
                labor_df['Regular Hours (h)'], 
                errors='coerce'
            )
            labor_df['Total Hours Worked (h)'] = pd.to_numeric(
                labor_df['Total Hours Worked (h)'], 
                errors='coerce'
            )
            
            for col in ['Gross Sales', 'Net Sales', 'Service Tips']:
                if col in productivity_df.columns:
                    productivity_df[col] = pd.to_numeric(
                        productivity_df[col].astype(str).str.replace('$', '').str.replace(',', '').str.strip(),
                        errors='coerce'
                    )
            
            tip_pool = 0
            processed_dfs = []
            roles = labor_df['Role'].unique()
            
            # First pass: Calculate tip pool from servers
            for role in roles:
                if role == 'Server':
                    role_df = labor_df[labor_df['Role'] == 'Server'].copy()
                    prod_data = productivity_df[productivity_df['Role'] == 'Server']
                    role_df = role_df.merge(
                        prod_data[['Match_Key', 'Gross Sales', 'Net Sales', 'Service Tips']],
                        on=['Match_Key'],
                        how='left'
                    )
                    
                    role_df['Gross Sales'] = role_df['Gross Sales'].fillna(0)
                    role_df['Service Tips'] = role_df['Service Tips'].fillna(0)
                    
                    # Use the appropriate tipout percentage based on mode
                    role_df['Tip Out'] = role_df['Gross Sales'] * self.tipout_percentage
                    tip_pool += role_df['Tip Out'].sum()
            
            # Second pass: Process all roles with the calculated tip pool
            for role in roles:
                role_df = labor_df[labor_df['Role'] == role].copy()
                
                # Merge with break data for all roles
                role_df = role_df.merge(
                    break_df[['Match_Key', 'break_count', 'total_break_minutes', 'total_break_hours', 
                              'days_without_break', 'days_with_break', 'has_unpaid_break']],
                    on=['Match_Key'],
                    how='left'
                )
                
                # Fill NaN values for break data
                role_df['break_count'] = role_df['break_count'].fillna(0)
                role_df['total_break_minutes'] = role_df['total_break_minutes'].fillna(0)
                role_df['total_break_hours'] = role_df['total_break_hours'].fillna(0)
                role_df['days_without_break'] = role_df['days_without_break'].fillna(0)
                role_df['days_with_break'] = role_df['days_with_break'].fillna(0)
                role_df['has_unpaid_break'] = role_df['has_unpaid_break'].fillna(False)
                
                # Store Regular Hours as the original value
                role_df['Regular Hours Original'] = role_df['Regular Hours (h)'].copy()
                
                # Subtract break hours from Regular Hours to get Total Hours Worked
                role_df['Break Hours'] = role_df['total_break_hours']
                
                # Total Hours Worked = Regular Hours - Break Hours (only subtract program-added breaks)
                # Note: The break hours from the timecard are already accounted for in Regular Hours
                # So we only subtract the program-added breaks (which are already in Break Hours)
                role_df['Total Hours Worked (h)'] = role_df['Regular Hours Original'] - role_df['Break Hours']
                
                # Ensure Total Hours Worked doesn't go negative
                role_df['Total Hours Worked (h)'] = role_df['Total Hours Worked (h)'].clip(lower=0)
                
                # Round to 2 decimal places
                role_df['Total Hours Worked (h)'] = role_df['Total Hours Worked (h)'].round(2)
                
                if role == 'Server':
                    # Merge with productivity data using Match_Key
                    prod_data = productivity_df[productivity_df['Role'] == 'Server']
                    role_df = role_df.merge(
                        prod_data[['Match_Key', 'Gross Sales', 'Net Sales', 'Service Tips']],
                        on=['Match_Key'],
                        how='left'
                    )
                    
                    role_df['Gross Sales'] = role_df['Gross Sales'].fillna(0)
                    role_df['Service Tips'] = role_df['Service Tips'].fillna(0)
                    
                    # Use the appropriate tipout percentage based on mode
                    role_df['Tip Out'] = role_df['Gross Sales'] * self.tipout_percentage
                    role_df['Tip-Out Tips'] = 0
                    role_df['Gross Tips'] = role_df['Service Tips'] - role_df['Tip Out']
                    role_df['Merchant Fee'] = role_df['Gross Tips'] * 0.03
                    role_df['Total Tips'] = role_df['Service Tips'] - role_df['Tip Out'] - role_df['Merchant Fee']
                    
                    role_df['Estimated Total Pay'] = role_df['Total Hours Worked (h)'] * role_df['Hourly Rate']
                    role_df['No. of Breaks'] = role_df['break_count']
                    role_df['Total Break Time'] = role_df['Break Hours'].round(2)
                    role_df['Net Sales'] = role_df['Net Sales'].fillna(0)
                    
                elif role == 'Bartender':
                    # Merge with productivity data for Service Tips
                    prod_data = productivity_df[productivity_df['Role'] == 'Bartender']
                    role_df = role_df.merge(
                        prod_data[['Match_Key', 'Service Tips']],
                        on=['Match_Key'],
                        how='left'
                    )
                    
                    role_df['Service Tips'] = role_df['Service Tips'].fillna(0)
                    
                    # Get the total percentage for bartender
                    bartender_pct = self.percentages.get('Bartender', 0.025)
                    # Calculate total pool money for bartender
                    bartender_pool = tip_pool * bartender_pct
                    
                    # Calculate total hours for bartenders
                    total_bartender_hours = role_df['Total Hours Worked (h)'].sum()
                    
                    if total_bartender_hours > 0:
                        # Calculate hourly tip rate for bartender
                        hourly_tip_rate = bartender_pool / total_bartender_hours
                        
                        # Calculate each person's tip-out tips based on their hours
                        role_df['Tip-Out Tips'] = role_df['Total Hours Worked (h)'] * hourly_tip_rate
                    else:
                        role_df['Tip-Out Tips'] = 0
                    
                    # Gross Tips = Service Tips + Tip-Out Tips
                    role_df['Gross Tips'] = role_df['Service Tips'] + role_df['Tip-Out Tips']
                    
                    # Merchant Fee = Gross Tips * 0.03
                    role_df['Merchant Fee'] = role_df['Gross Tips'] * 0.03
                    
                    # Total Tips = Gross Tips - Merchant Fee
                    role_df['Total Tips'] = role_df['Gross Tips'] - role_df['Merchant Fee']
                    
                    # Tip Out = 0 (bartenders don't tip out)
                    role_df['Tip Out'] = 0
                    
                    role_df['Estimated Total Pay'] = role_df['Total Hours Worked (h)'] * role_df['Hourly Rate']
                    role_df['No. of Breaks'] = role_df['break_count']
                    role_df['Total Break Time'] = role_df['Break Hours'].round(2)
                    role_df['Gross Sales'] = 0
                    role_df['Net Sales'] = 0
                    
                elif role in ['Busser', 'Food Runner', 'Food-Bar Runner', 'Food-Bar Prep', 'Cashier/Host']:
                    # Get the total percentage for this role
                    role_pct = self.percentages.get(role, 0)
                    # Calculate total pool money for this role
                    role_pool = tip_pool * role_pct
                    
                    # Calculate total hours for this role
                    total_role_hours = role_df['Total Hours Worked (h)'].sum()
                    
                    if total_role_hours > 0:
                        # Calculate hourly tip rate for this role
                        hourly_tip_rate = role_pool / total_role_hours
                        
                        # Calculate each person's tip-out tips based on their hours
                        role_df['Tip-Out Tips'] = role_df['Total Hours Worked (h)'] * hourly_tip_rate
                    else:
                        role_df['Tip-Out Tips'] = 0
                    
                    # Gross Tips = Tip-Out Tips
                    role_df['Gross Tips'] = role_df['Tip-Out Tips']
                    
                    # Merchant Fee = Gross Tips * 0.03
                    role_df['Merchant Fee'] = role_df['Gross Tips'] * 0.03
                    
                    # Total Tips = Gross Tips - Merchant Fee
                    role_df['Total Tips'] = role_df['Gross Tips'] - role_df['Merchant Fee']
                    
                    role_df['Estimated Total Pay'] = role_df['Total Hours Worked (h)'] * role_df['Hourly Rate']
                    role_df['No. of Breaks'] = role_df['break_count']
                    role_df['Total Break Time'] = role_df['Break Hours'].round(2)
                    role_df['Gross Sales'] = 0
                    role_df['Net Sales'] = 0
                    role_df['Service Tips'] = 0
                    role_df['Tip Out'] = 0
                    
                else:
                    # Non-tipped roles (trainees, dishwashers, prep cooks)
                    role_df['Estimated Total Pay'] = role_df['Total Hours Worked (h)'] * role_df['Hourly Rate']
                    role_df['No. of Breaks'] = role_df['break_count']
                    role_df['Total Break Time'] = role_df['Break Hours'].round(2)
                    role_df['Gross Sales'] = 0
                    role_df['Net Sales'] = 0
                    role_df['Service Tips'] = 0
                    role_df['Tip Out'] = 0
                    role_df['Tip-Out Tips'] = 0
                    role_df['Gross Tips'] = 0
                    role_df['Merchant Fee'] = 0
                    role_df['Total Tips'] = 0
                
                # Drop the temporary columns
                role_df = role_df.drop(['Regular Hours Original', 'Break Hours'], axis=1, errors='ignore')
                
                # Keep only the original columns plus new ones
                processed_dfs.append(role_df)
            
            all_employees = pd.concat(processed_dfs, ignore_index=True)
            
            all_employees['Effective Hourly Rate'] = all_employees.apply(
                lambda row: (row['Estimated Total Pay'] + row['Total Tips']) / row['Total Hours Worked (h)']
                if row['Total Hours Worked (h)'] > 0 else 0,
                axis=1
            )
            
            all_employees['Gross Pay'] = all_employees['Estimated Total Pay'] + all_employees['Total Tips']
            # Leave Deductions, Uniforms, Bonus, Final Pay blank/empty
            all_employees['Deductions'] = ''
            all_employees['Uniforms'] = ''
            all_employees['Bonus'] = ''
            all_employees['Final Pay'] = ''
            
            column_order = [
                'Name', 'Role', 'Hourly Rate', 'Regular Hours (h)', 
                'No. of Breaks', 'Total Break Time', 'Total Hours Worked (h)',
                'Estimated Total Pay', 'Gross Sales', 'Net Sales', 
                'Service Tips', 'Tip Out', 'Tip-Out Tips', 'Gross Tips',
                'Merchant Fee', 'Total Tips', 'Gross Pay', 
                'Deductions', 'Uniforms', 'Bonus', 'Final Pay',
                'Effective Hourly Rate'
            ]
            
            for col in column_order:
                if col not in all_employees.columns:
                    all_employees[col] = '' if col in ['Deductions', 'Uniforms', 'Bonus', 'Final Pay'] else 0
            
            result_df = all_employees[column_order]
            
            numeric_cols = ['Hourly Rate', 'Regular Hours (h)', 'Total Hours Worked (h)',
                          'Estimated Total Pay', 'Gross Sales', 'Net Sales', 'Service Tips',
                          'Tip Out', 'Tip-Out Tips', 'Gross Tips', 'Merchant Fee',
                          'Total Tips', 'Gross Pay', 'Effective Hourly Rate']
            
            for col in numeric_cols:
                if col in result_df.columns:
                    result_df[col] = result_df[col].round(2)
            
            self.processed_data = result_df
            return result_df
            
        except Exception as e:
            raise Exception(f"Error processing payroll: {str(e)}\n{traceback.format_exc()}")

    def format_output_csv(self):
        """
        Format the processed data with role-based grouping.
        Only Servers get a TOTAL row with column sums.
        Includes date range header.
        """
        if self.processed_data is None:
            return None
        
        # Get the list of roles that exist in the data
        existing_roles = self.processed_data['Role'].unique()
        
        # Create a list to store all formatted dataframes
        formatted_dfs = []
        
        # Define columns for the output
        columns = [
            'Name', 'Role', 'Hourly Rate', 'Regular Hours (h)', 
            'No. of Breaks', 'Total Break Time', 'Total Hours Worked (h)',
            'Estimated Total Pay', 'Gross Sales', 'Net Sales', 
            'Service Tips', 'Tip Out', 'Tip-Out Tips', 'Gross Tips',
            'Merchant Fee', 'Total Tips', 'Gross Pay', 
            'Deductions', 'Uniforms', 'Bonus', 'Final Pay',
            'Effective Hourly Rate'
        ]
        
        # Numeric columns for summing
        numeric_cols = [
            'Regular Hours (h)', 'No. of Breaks', 'Total Break Time', 'Total Hours Worked (h)',
            'Estimated Total Pay', 'Gross Sales', 'Net Sales', 'Service Tips',
            'Tip Out', 'Tip-Out Tips', 'Gross Tips', 'Merchant Fee',
            'Total Tips', 'Gross Pay'
        ]
        
        # Process each role in the defined order
        for role in self.role_order:
            if role not in existing_roles:
                continue
            
            # Filter data for this role
            role_data = self.processed_data[self.processed_data['Role'] == role].copy()
            
            if role_data.empty:
                continue
            
            # Add the role data (round numeric columns to 2 decimal places)
            for col in numeric_cols:
                if col in role_data.columns:
                    role_data[col] = role_data[col].round(2)
            
            # For rate columns, also round to 2 decimal places
            for col in ['Hourly Rate', 'Effective Hourly Rate']:
                if col in role_data.columns:
                    role_data[col] = role_data[col].round(2)
            
            # Keep Deductions, Uniforms, Bonus, Final Pay as empty strings
            for col in ['Deductions', 'Uniforms', 'Bonus', 'Final Pay']:
                if col in role_data.columns:
                    role_data[col] = ''
            
            formatted_dfs.append(role_data)
            
            # ONLY add totals row for Servers
            if role == 'Server':
                # Create totals row for Servers
                totals_row = {'Name': 'TOTAL', 'Role': role}
                
                # Calculate sums for numeric columns (except Effective Hourly Rate)
                for col in numeric_cols:
                    if col in role_data.columns:
                        totals_row[col] = round(role_data[col].sum(), 2)
                
                # Handle rate columns (average instead of sum)
                for col in ['Hourly Rate', 'Effective Hourly Rate']:
                    if col in role_data.columns:
                        totals_row[col] = round(role_data[col].mean(), 2)
                
                # Keep Deductions, Uniforms, Bonus, Final Pay as empty strings
                for col in ['Deductions', 'Uniforms', 'Bonus', 'Final Pay']:
                    totals_row[col] = ''
                
                # Create a DataFrame for the totals row
                totals_df = pd.DataFrame([totals_row])
                
                # Ensure all columns are present
                for col in columns:
                    if col not in totals_df.columns:
                        totals_df[col] = '' if col in ['Deductions', 'Uniforms', 'Bonus', 'Final Pay'] else 0
                
                # Reorder columns
                totals_df = totals_df[columns]
                
                # Round all numeric values in totals row
                for col in numeric_cols:
                    if col in totals_df.columns:
                        totals_df[col] = totals_df[col].round(2)
                
                # Add the totals row
                formatted_dfs.append(totals_df)
            
            # Add a blank row (as an empty DataFrame with the same columns)
            blank_row = pd.DataFrame({col: [''] for col in columns})
            formatted_dfs.append(blank_row)
        
        # Remove the last blank row if it exists
        if len(formatted_dfs) > 0 and formatted_dfs[-1].iloc[0, 0] == '':
            formatted_dfs.pop()
        
        # Create the Grand Total row
        if formatted_dfs:
            # Concatenate all data for grand total calculation
            all_data = pd.concat(formatted_dfs, ignore_index=True)
            
            # Filter out empty rows and totals rows for calculation
            calc_data = all_data[
                (all_data['Name'] != 'TOTAL') & 
                (all_data['Name'] != '') & 
                (all_data['Name'].notna())
            ]
            
            # Create grand total row
            grand_total = {'Name': 'GRAND TOTAL', 'Role': ''}
            
            # Calculate sums for numeric columns (except Effective Hourly Rate)
            for col in numeric_cols:
                if col in calc_data.columns:
                    grand_total[col] = round(calc_data[col].sum(), 2)
            
            # Handle rate columns (average instead of sum)
            for col in ['Hourly Rate', 'Effective Hourly Rate']:
                if col in calc_data.columns:
                    grand_total[col] = round(calc_data[col].mean(), 2)
            
            # Keep Deductions, Uniforms, Bonus, Final Pay as empty strings
            for col in ['Deductions', 'Uniforms', 'Bonus', 'Final Pay']:
                grand_total[col] = ''
            
            grand_total_df = pd.DataFrame([grand_total])
            
            # Ensure all columns are present
            for col in columns:
                if col not in grand_total_df.columns:
                    grand_total_df[col] = '' if col in ['Deductions', 'Uniforms', 'Bonus', 'Final Pay'] else 0
            
            grand_total_df = grand_total_df[columns]
            
            # Round all numeric values in grand total
            for col in numeric_cols:
                if col in grand_total_df.columns:
                    grand_total_df[col] = grand_total_df[col].round(2)
            
            # Add grand total
            formatted_dfs.append(grand_total_df)
        
        # Concatenate everything
        final_df = pd.concat(formatted_dfs, ignore_index=True)
        
        return final_df

    def save_to_downloads(self):
        """Save the formatted output directly to Downloads folder"""
        if self.processed_data is None:
            return None, None
        
        formatted_df = self.format_output_csv()
        if formatted_df is None:
            return None, None
        
        # Get the user's Downloads folder
        downloads_path = os.path.expanduser("~/Downloads")
        
        # Create filename with mode and date
        mode_lower = self.mode.lower()
        # Get current date for filename
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d_%H-%M')
        filename = f"processed_payroll_{mode_lower}_{date_str}.csv"
        file_path = os.path.join(downloads_path, filename)
        
        # Create a header row with the date range
        header_df = pd.DataFrame([[f'Date Range: {self.date_range if self.date_range else ""}'] + [''] * (len(formatted_df.columns) - 1)], 
                                columns=formatted_df.columns)
        
        # Combine header and data
        final_df = pd.concat([header_df, formatted_df], ignore_index=True)
        
        # Save to Downloads
        final_df.to_csv(file_path, index=False)
        
        return file_path, len(formatted_df)


class PayrollApp:
    """Main application window using tkinter"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Payroll Processor")
        self.root.geometry("600x650")
        
        self.processor = None
        self.labor_file = None
        self.productivity_file = None
        self.timecard_file = None
        self.processing = False
        
        self.setup_ui()
        
    def setup_ui(self):
        """Setup the user interface"""
        # Main container
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        header = ttk.Label(main_frame, text="Payroll Processing Application", 
                          font=('Arial', 18, 'bold'))
        header.pack(pady=(0, 20))
        
        # Mode selection
        mode_frame = ttk.LabelFrame(main_frame, text="Mode Selection", padding=10)
        mode_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.mode_var = tk.StringVar(value="MAAX")
        mode_combo = ttk.Combobox(mode_frame, textvariable=self.mode_var, 
                                  values=["MAAX", "Tomahawk"], state="readonly")
        mode_combo.pack(fill=tk.X)
        mode_combo.bind('<<ComboboxSelected>>', self.on_mode_change)
        
        # File upload section
        file_frame = ttk.LabelFrame(main_frame, text="Upload Files", padding=10)
        file_frame.pack(fill=tk.X, pady=(0, 15))
        
        # Labor file
        self.labor_btn = ttk.Button(file_frame, text="📁 Load Labor Summary CSV",
                                   command=lambda: self.load_file("labor"))
        self.labor_btn.pack(fill=tk.X, pady=(0, 2))
        self.labor_status = ttk.Label(file_frame, text="No file loaded", foreground="gray")
        self.labor_status.pack(anchor=tk.W, pady=(0, 5))
        
        # Productivity file
        self.productivity_btn = ttk.Button(file_frame, text="📁 Load Productivity CSV",
                                          command=lambda: self.load_file("productivity"))
        self.productivity_btn.pack(fill=tk.X, pady=(0, 2))
        self.productivity_status = ttk.Label(file_frame, text="No file loaded", foreground="gray")
        self.productivity_status.pack(anchor=tk.W, pady=(0, 5))
        
        # Timecard file
        self.timecard_btn = ttk.Button(file_frame, text="📁 Load Time Card CSV",
                                      command=lambda: self.load_file("timecard"))
        self.timecard_btn.pack(fill=tk.X, pady=(0, 2))
        self.timecard_status = ttk.Label(file_frame, text="No file loaded", foreground="gray")
        self.timecard_status.pack(anchor=tk.W, pady=(0, 5))
        
        # Tip percentage section
        self.percentage_frame = ttk.LabelFrame(main_frame, text="Tip Pool Percentages", padding=10)
        self.percentage_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.percentage_widgets = {}
        self.setup_percentage_fields("MAAX")
        
        # Process button (now the only button needed)
        self.process_btn = ttk.Button(main_frame, text="🚀 Process Payroll & Download",
                                     command=self.process_and_download, state=tk.DISABLED)
        self.process_btn.pack(fill=tk.X, pady=(0, 5))
        
        # Progress bar
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=(0, 5))
        self.progress.pack_forget()
        
        # Status log
        log_frame = ttk.LabelFrame(main_frame, text="Status Log", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        self.log("Application started. Select mode and load files.")
    
    def setup_percentage_fields(self, mode):
        """Setup tip percentage fields based on mode"""
        # Clear existing widgets
        for widget in self.percentage_frame.winfo_children():
            widget.destroy()
        
        self.percentage_widgets = {}
        
        if mode == "MAAX":
            roles = ["Busser", "Food Runner", "Food-Bar Runner", "Food-Bar Prep", "Cashier/Host", "Bartender"]
            defaults = [42.0, 19.0, 29.0, 4.5, 3.0, 2.5]
        else:  # Tomahawk
            roles = ["Busser", "Cashier/Host", "Bartender"]
            defaults = [42.0, 3.0, 2.5]
        
        for role, default in zip(roles, defaults):
            frame = ttk.Frame(self.percentage_frame)
            frame.pack(fill=tk.X, pady=2)
            
            label = ttk.Label(frame, text=f"{role}:", width=15)
            label.pack(side=tk.LEFT)
            
            var = tk.DoubleVar(value=default)
            spinbox = ttk.Spinbox(frame, from_=0, to=100, increment=0.5, 
                                 textvariable=var, width=10)
            spinbox.pack(side=tk.LEFT, padx=(0, 5))
            
            pct_label = ttk.Label(frame, text="%")
            pct_label.pack(side=tk.LEFT)
            
            self.percentage_widgets[role] = var
    
    def on_mode_change(self, event=None):
        """Handle mode change"""
        mode = self.mode_var.get()
        self.setup_percentage_fields(mode)
        self.log(f"Switched to {mode} mode")
        if self.processor:
            self.processor.mode = mode
            self.processor.percentages = self.processor.default_percentages.copy()
            # Update tipout percentage based on mode
            if mode == "MAAX":
                self.processor.tipout_percentage = 0.07
            else:  # Tomahawk
                self.processor.tipout_percentage = 0.05
    
    def load_file(self, file_type):
        """Load a file based on type"""
        file_path = filedialog.askopenfilename(
            title=f"Select {file_type.capitalize()} CSV File",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        
        if not file_path:
            return
        
        try:
            if file_type == "labor":
                self.labor_file = file_path
                self.labor_status.config(text=f"✓ {os.path.basename(file_path)}", foreground="green")
                if not self.processor:
                    self.processor = PayrollProcessor(self.mode_var.get())
                self.processor.load_labor_data(file_path)
                self.log(f"Loaded labor data: {os.path.basename(file_path)}")
                
            elif file_type == "productivity":
                self.productivity_file = file_path
                self.productivity_status.config(text=f"✓ {os.path.basename(file_path)}", foreground="green")
                if not self.processor:
                    self.processor = PayrollProcessor(self.mode_var.get())
                self.processor.load_productivity_data(file_path)
                self.log(f"Loaded productivity data: {os.path.basename(file_path)}")
                
            elif file_type == "timecard":
                self.timecard_file = file_path
                self.timecard_status.config(text=f"✓ {os.path.basename(file_path)}", foreground="green")
                if not self.processor:
                    self.processor = PayrollProcessor(self.mode_var.get())
                self.processor.load_timecard_data(file_path)
                self.log(f"Loaded timecard data: {os.path.basename(file_path)}")
            
            # Check if all files are loaded
            if all([self.labor_file, self.productivity_file, self.timecard_file]):
                self.process_btn.config(state=tk.NORMAL)
                self.log("All files loaded. Ready to process.")
            
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.log(f"Error loading file: {str(e)}")
    
    def process_and_download(self):
        """Process payroll and automatically download to Downloads folder"""
        if self.processing:
            return
        
        if not self.processor:
            messagebox.showwarning("Warning", "Please load all files first.")
            return
        
        # Update percentages from spinboxes
        for role, var in self.percentage_widgets.items():
            self.processor.percentages[role] = var.get() / 100.0
        
        self.process_btn.config(state=tk.DISABLED)
        self.progress.pack(fill=tk.X, pady=(0, 5))
        self.progress.start()
        self.processing = True
        
        self.log("Processing payroll...")
        
        # Run processing in a thread
        thread = threading.Thread(target=self._process_and_download_thread)
        thread.daemon = True
        thread.start()
    
    def _process_and_download_thread(self):
        """Processing and download thread function"""
        error_msg = None
        file_path = None
        num_rows = 0
        
        try:
            # Process the payroll
            result = self.processor.process_payroll()
            
            if result is not None:
                # Save to Downloads
                file_path, num_rows = self.processor.save_to_downloads()
                
                if not file_path:
                    error_msg = "Failed to save file."
            else:
                error_msg = "Processing failed - no data generated."
                
        except Exception as e:
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
        
        # Update UI in the main thread
        if error_msg:
            self.root.after(0, lambda: self.on_processing_error(error_msg))
        else:
            self.root.after(0, lambda: self.on_download_success(file_path, num_rows))
    
    def on_download_success(self, file_path, num_rows):
        """Handle successful processing and download"""
        self.progress.stop()
        self.progress.pack_forget()
        self.process_btn.config(state=tk.NORMAL)
        self.processing = False
        
        self.log(f"✓ Processing complete! {num_rows} rows processed.")
        self.log(f"✓ File saved to: {file_path}")
        
        messagebox.showinfo("Success", 
                           f"Payroll processed successfully!\n"
                           f"{num_rows} rows processed.\n\n"
                           f"File saved to:\n{file_path}")
    
    def on_processing_error(self, error_msg):
        """Handle processing error"""
        self.progress.stop()
        self.progress.pack_forget()
        self.process_btn.config(state=tk.NORMAL)
        self.processing = False
        
        # Show only the first part of the error in the messagebox
        display_error = error_msg.split('\n')[0] if '\n' in error_msg else error_msg
        messagebox.showerror("Error", display_error)
        self.log(f"Error: {error_msg}")
    
    def log(self, message):
        """Add message to log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)


def main():
    root = tk.Tk()
    app = PayrollApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()