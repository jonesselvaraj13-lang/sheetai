import pandas as pd
from pathlib import Path
from tempfile import TemporaryDirectory
from django.test import SimpleTestCase

from .utils import process_prompt, read_file


class PromptAnalysisTests(SimpleTestCase):
    def test_count_by_category_uses_full_dataframe(self):
        df = pd.DataFrame({
            'Region': ['South', 'North', 'South', 'West'],
            'Sales': [10, 20, 15, 5],
        })

        result = process_prompt(df, 'count by region')

        south_count = result.loc[result['Region'] == 'South', 'Count'].iloc[0]
        self.assertEqual(south_count, 2)

    def test_total_metric_by_category(self):
        df = pd.DataFrame({
            'Region': ['South', 'North', 'South', 'West'],
            'Sales': [10, 20, 15, 5],
        })

        result = process_prompt(df, 'total sales by region')

        south_total = result.loc[result['Region'] == 'South', 'Total Sales'].iloc[0]
        self.assertEqual(south_total, 25)

    def test_text_filter_returns_matching_rows(self):
        df = pd.DataFrame({
            'Name': ['Anu', 'Bala', 'Chen'],
            'Department': ['CSE', 'ECE', 'CSE'],
        })

        result = process_prompt(df, 'show CSE students')

        self.assertEqual(len(result), 2)
        self.assertEqual(set(result['Name']), {'Anu', 'Chen'})

    def test_plain_total_question_returns_direct_answer(self):
        df = pd.DataFrame({
            'Region': ['South', 'North', 'South', 'West'],
            'Sales': [10, 20, 15, 5],
        })

        result = process_prompt(df, 'what is total sales')

        self.assertEqual(result.iloc[0]['Value'], 50)

    def test_highest_question_returns_supporting_row(self):
        df = pd.DataFrame({
            'Name': ['Anu', 'Bala', 'Chen'],
            'Marks': [78, 92, 85],
        })

        result = process_prompt(df, 'who has highest marks')

        self.assertEqual(result.iloc[0]['Name'], 'Bala')
        self.assertIn('highest Marks is 92', result.iloc[0]['Answer'])

    def test_percentage_by_category(self):
        df = pd.DataFrame({
            'Department': ['CSE', 'ECE', 'CSE', 'MECH'],
        })

        result = process_prompt(df, 'percentage by department')

        cse_percentage = result.loc[result['Department'] == 'CSE', 'Percentage'].iloc[0]
        self.assertEqual(cse_percentage, 50.0)

    def test_numeric_text_columns_are_calculated_correctly(self):
        df = pd.DataFrame({
            'Product': ['A', 'B', 'C'],
            'Revenue': ['1,200', '800', '2,000'],
        })

        result = process_prompt(df, 'total revenue')

        self.assertEqual(result.iloc[0]['Value'], 4000)

    def test_uploaded_csv_uses_all_rows_for_prompt_answers(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'large.csv'
            pd.DataFrame({'Value': range(6001)}).to_csv(path, index=False)

            df = read_file(str(path))
            result = process_prompt(df, 'how many rows')

        self.assertEqual(result.iloc[0]['Rows'], 6001)

    def test_table_prompt_uses_dataframe_before_url_text(self):
        df = pd.DataFrame({
            'Region': ['South', 'North', 'South'],
            'Sales': [10, 20, 5],
        })

        result = process_prompt(df, 'total sales by region', raw_text='This page mentions unrelated sales text.')

        south_total = result.loc[result['Region'] == 'South', 'Total Sales'].iloc[0]
        self.assertEqual(south_total, 15)

    def test_analyze_metric_by_category_groups_table(self):
        df = pd.DataFrame({
            'Region': ['South', 'North', 'South'],
            'Sales': [10, 20, 5],
        })

        result = process_prompt(df, 'analyze sales by region')

        south_total = result.loc[result['Region'] == 'South', 'Total Sales'].iloc[0]
        self.assertEqual(south_total, 15)
