import pandas as pd
import sys

def read_excel(file_path):
    try:
        # Read the Excel file
        df = pd.read_excel(file_path)
        
        # Display basic information
        print(f"=== File Info: {file_path} ===")
        print(f"Total Rows: {len(df)}")
        print(f"Columns: {', '.join(df.columns)}")
        print("\n=== First 5 rows ===")
        
        # Convert to a string representation that's easy to read
        # Using string conversion for all columns to avoid formatting issues
        df_str = df.head(5).astype(str)
        
        # Print column names with a separator
        header = " | ".join(df_str.columns)
        print(header)
        print("-" * len(header))
        
        # Print each row
        for _, row in df_str.iterrows():
            print(" | ".join(row.values))
            
    except Exception as e:
        print(f"Error reading Excel file: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        read_excel(sys.argv[1])
    else:
        print("Please provide an Excel file path.")
