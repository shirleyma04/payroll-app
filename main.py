import customtkinter as ctk
from tkinter import filedialog
from tkinter import messagebox

from payroll_logic import process_payroll
from payroll_logic import ROLE_PERCENTAGES


ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')


class PayrollApp(ctk.CTk):

    def __init__(self):

        super().__init__()

        self.title('Payroll Processor')
        self.geometry('900x750')

        self.productivity_file = None
        self.labor_file = None
        self.timecard_file = None

        self.percentage_entries = {}

        self.create_ui()

    # ---------------------------------
    # UI
    # ---------------------------------

    def create_ui(self):

        title = ctk.CTkLabel(
            self,
            text='Payroll Processor',
            font=('Arial', 32, 'bold')
        )

        title.pack(pady=20)

        subtitle = ctk.CTkLabel(
            self,
            text='Upload payroll files and calculate payroll automatically.',
            font=('Arial', 16)
        )

        subtitle.pack(pady=10)

        # -----------------------------
        # Upload Buttons
        # -----------------------------

        upload_productivity_btn = ctk.CTkButton(
            self,
            text='Upload Productivity CSV',
            command=self.upload_productivity
        )

        upload_productivity_btn.pack(pady=10)

        self.productivity_label = ctk.CTkLabel(
            self,
            text='No productivity file selected'
        )

        self.productivity_label.pack()

        upload_labor_btn = ctk.CTkButton(
            self,
            text='Upload Labor Summary CSV',
            command=self.upload_labor
        )

        upload_labor_btn.pack(pady=10)

        self.labor_label = ctk.CTkLabel(
            self,
            text='No labor file selected'
        )

        self.labor_label.pack()

        upload_timecard_btn = ctk.CTkButton(
            self,
            text='Upload Timecard CSV',
            command=self.upload_timecard
        )

        upload_timecard_btn.pack(pady=10)

        self.timecard_label = ctk.CTkLabel(
            self,
            text='No time card file selected'
        )

        self.timecard_label.pack()

        # -----------------------------
        # Percentages
        # -----------------------------

        percentages_title = ctk.CTkLabel(
            self,
            text='Role Percentages',
            font=('Arial', 24, 'bold')
        )

        percentages_title.pack(pady=20)

        percentages_frame = ctk.CTkScrollableFrame(self, height=200)
        percentages_frame.pack(pady=10, padx=20, fill='x')

        for role, pct in ROLE_PERCENTAGES.items():

            row = ctk.CTkFrame(percentages_frame)
            row.pack(fill='x', pady=5, padx=10)

            label = ctk.CTkLabel(
                row,
                text=role,
                width=200,
                anchor='w'
            )

            label.pack(side='left', padx=10)

            entry = ctk.CTkEntry(row, width=100)
            entry.insert(0, str(pct))
            entry.pack(side='right', padx=10)

            self.percentage_entries[role] = entry

        # -----------------------------
        # Process Button
        # -----------------------------

        process_btn = ctk.CTkButton(
            self,
            text='Process Payroll',
            height=50,
            font=('Arial', 18, 'bold'),
            command=self.process_payroll
        )

        process_btn.pack(pady=30)

    # ---------------------------------
    # FILE UPLOADS
    # ---------------------------------

    def upload_productivity(self):

        file_path = filedialog.askopenfilename(
            filetypes=[('CSV Files', '*.csv')]
        )

        if file_path:
            self.productivity_file = file_path
            self.productivity_label.configure(text=file_path.split('/')[-1])

    def upload_labor(self):

        file_path = filedialog.askopenfilename(
            filetypes=[('CSV/Excel Files', '*.csv *.xlsx')]
        )

        if file_path:
            self.labor_file = file_path
            self.labor_label.configure(text=file_path.split('/')[-1])

    def upload_timecard(self):

        file_path = filedialog.askopenfilename(
            filetypes=[('CSV Files', '*.csv')]
        )

        if file_path:
            self.timecard_file = file_path
            self.timecard_label.configure(text=file_path.split('/')[-1])

    # ---------------------------------
    # PROCESS PAYROLL
    # ---------------------------------

    def process_payroll(self):

        try:

            if not self.productivity_file:
                messagebox.showerror(
                    'Missing File',
                    'Please upload the Productivity CSV.'
                )
                return

            if not self.labor_file:
                messagebox.showerror(
                    'Missing File',
                    'Please upload the Labor CSV/Excel.'
                )
                return

            if not self.timecard_file:
                messagebox.showerror(
                    'Missing File',
                    'Please upload the Time Card CSV.'
                )
                return

            percentages = {}

            for role, entry in self.percentage_entries.items():
                try:
                    percentages[role] = float(entry.get())
                except ValueError:
                    messagebox.showerror(
                        'Invalid Percentage',
                        f'Please enter a valid number for {role} percentage.'
                    )
                    return

            # Validate percentages sum to 1.0
            total = sum(percentages.values())
            if abs(total - 1.0) > 0.01:
                response = messagebox.askyesno(
                    'Percentage Sum Warning',
                    f'Total percentages sum to {total:.2f}, not 1.00.\n\n'
                    'Do you want to continue anyway?'
                )
                if not response:
                    return

            output_path = process_payroll(
                self.productivity_file,
                self.labor_file,
                self.timecard_file,
                percentages
            )

            messagebox.showinfo(
                'Success',
                f'Payroll processed successfully!\n\nSaved to:\n{output_path}'
            )

        except Exception as e:

            messagebox.showerror(
                'Error',
                f'An error occurred:\n\n{str(e)}'
            )


if __name__ == '__main__':

    app = PayrollApp()
    app.mainloop()