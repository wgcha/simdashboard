from typing import List, Dict, Any, Optional

class VerdictService:
    def evaluate_scalar(self, scalar: Dict[str, Any], project_thresholds: List[Dict[str, Any]]) -> str:
        # 1. criterion_key로 project thresholds 조회
        threshold = None
        criterion_key = scalar.get("criterion_key")
        if criterion_key:
            for pt in project_thresholds:
                if pt["criterion_key"] == criterion_key:
                    threshold = pt["threshold_double"]
                    break
                    
        # 2. 파일의 threshold
        if threshold is None:
            threshold = scalar.get("threshold_double")
            
        if threshold is None:
            return "NO_CRITERION"
            
        value = scalar["value_double"]
        if value <= threshold:
            return "PASS"
        else:
            return "FAIL"

    def determine_overall_verdict(self, scalar_results: List[Dict[str, Any]], expected_required: bool = True) -> str:
        if not scalar_results:
            return "NO_DATA"
            
        has_fail = False
        has_no_criterion = False
        
        for sr in scalar_results:
            verdict = sr.get("verdict")
            if verdict == "FAIL":
                has_fail = True
            elif verdict == "NO_CRITERION":
                has_no_criterion = True
                
        if has_fail:
            return "FAIL"
            
        # For simplicity in MVP, if there are results and no fails, it's PASS
        return "PASS"
