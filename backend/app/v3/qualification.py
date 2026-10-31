def matching_format_checks(rows,**context):
    """Only reuse parser evidence for the exact endpoint, collection, image and model."""
    return [row for row in rows if row.get('extension') in {'.pdf','.docx','.xlsx'}
        and row.get('status')=='passed' and row.get('fact_preserved') is True
        and all(row.get(key)==value for key,value in context.items())]
