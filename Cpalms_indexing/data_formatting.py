import json
from collections import defaultdict

def consolidate_resource_json(resource_core, lesson_plan_template, resource_benchmarks, resource_files,benchmark_des):

    files_lookup = defaultdict(list)
    for f in resource_files:
        files_lookup[f['ResourceID']].append(f)

    lesson_plan_lookup = defaultdict(list)
    for l in lesson_plan_template:
        lesson_plan_lookup[l['ResourceID']].append(l)

    consolidated_list = []
    for r in resource_core:
        rid = r['ResourceID']

        benchmark_ids = [b['BenchmarkID'] for b in resource_benchmarks if 'BenchmarkID' in b]
        benchmark_codes = [b['Code'] for b in resource_benchmarks if 'Code' in b]
        benchmark_ids_str = ",".join(str(bid) for bid in benchmark_ids)
        benchmark_codes_str =",".join(str(bid) for bid in benchmark_codes)
        # Files
        files = []
        for f in files_lookup.get(rid, []):
            files.append({
                "FileTitle": f.get("FileTitle", ""),
                "FileDescription": f.get("FileDescription", ""),
                "FinalPath": f.get("FinalPath", "")
            })
        
        # Lesson Plan Q&A
        lesson_plan_qna = []
        for lp in lesson_plan_lookup.get(rid, []):
            lesson_plan_qna.append({
                "Title": lp.get("Title", ""),
                "ResLessPlanQuestionAnswer": lp.get("ResLessPlanQuestionAnswer", "")
            })
        
        consolidated = {
            "ResourceID": rid,
            "Title": r.get("Title", ""),
            "Description": r.get("Description", ""),
            "ResourceTypeId": r.get("ResourceTypeID", None),
            "Accomodation": r.get("Accomodation", ""),
            "Extensions": r.get("Extensions", ""),
            "FurtherRecommendations": r.get("FurtherRecommendations", None),
            "SpecialMaterialsNeeded": r.get("SpecialMaterialsNeeded", ""),
            "PublishedDate": str(r.get("PublishedDate", "")),
            "PrimaryResourceICTId": r.get("PrimaryResourceICTId", None),
            "PrimaryICT": r.get("PrimaryICT", ""),
            "GradeLevelNames": r.get("GradeLevelNames", ""),
            "SubjectAreaNames": r.get("SubjectAreaNames", ""),
            "IntendedAudienceNames": r.get("IntendedAudienceNames", ""),
            "Benchmarks": r.get("Benchmarks", ""),
            "ResourceUrl": r.get("ResourceUrl", ""),
            "BenchmarkIds": benchmark_ids_str,
            "BenchmarkCodes": benchmark_codes_str,
            "BenchmarkDescriptions": benchmark_des,
            "Files": files,
            "LessonPlanQuestions": lesson_plan_qna
        }
        
        consolidated_list.append(consolidated)
        
    return consolidated_list