#RADIOSS STARTER
#Runname      
/PARAMETER/GLOBAL/REAL/1
                                                                                                    
param1                  0.0572
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
/PARAMETER/GLOBAL/REAL/2
                                                                                                    
param2                  0.0572
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
/PARAMETER/GLOBAL/REAL/3
                                                                                                    
LC_E_Z                    0.05
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
/PARAMETER/GLOBAL/REAL/4
                                                                                                    
LC_G                       2.0
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
/PARAMETER/GLOBAL/REAL_EXPR/5
                                                                                                    
LC_E      2*LC_G                                                                       
/BEGIN
#Runname                                                                        
101_parts.inc                                                                   
#   Invers      Irun
      2024         0
#    Input_mass_unit   Input_length_unit     Input_time_unit
                  Mg                  mm                   s
#     Work_mass_unit    Work_length_unit      Work_time_unit
                  Mg                  mm                   s
`#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|`
#   Invers      Irun
#    Input_mass_unit   Input_length_unit     Input_time_unit
#     Work_mass_unit    Work_length_unit      Work_time_unit
/PART/2
#part_title                                                                                         
board_main                                                                                          
#  prop_ID    mat_ID subset_ID               Thick
         1     50230        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 4 11
/PART/4
#part_title                                                                                         
board_dcdc                                                                                          
#  prop_ID    mat_ID subset_ID               Thick
         2     50230        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 8 13
/PART/8
#part_title                                                                                         
cell_tape                                                                                           
#  prop_ID    mat_ID subset_ID               Thick
         3     51104        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 10 59
/PART/10
#part_title                                                                                         
chassis_rear                                                                                        
#  prop_ID    mat_ID subset_ID               Thick
         4     51107        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 12 54
/PART/12
#part_title                                                                                         
cover_decoration                                                                                    
#  prop_ID    mat_ID subset_ID               Thick
         6     50250        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 15 60
/PART/15
#part_title                                                                                         
holder_cell                                                                                         
#  prop_ID    mat_ID subset_ID               Thick
         8     51116        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 18 59
/PART/18
#part_title                                                                                         
holder_dp                                                                                           
#  prop_ID    mat_ID subset_ID               Thick
        10     50250        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 21 10
/PART/21
#part_title                                                                                         
holder_sheet                                                                                        
#  prop_ID    mat_ID subset_ID               Thick
        11     51010        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 23 37
/PART/23
#part_title                                                                                         
plate_d                                                                                             
#  prop_ID    mat_ID subset_ID               Thick
        12     50130        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 25 26
/PART/25
#part_title                                                                                         
sheet_mop                                                                                           
#  prop_ID    mat_ID subset_ID               Thick
        13     50040        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 27 10
/PART/27
#part_title                                                                                         
spacer_conductive                                                                                   
#  prop_ID    mat_ID subset_ID               Thick
        15     51115        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 29 11
/PART/29
#part_title                                                                                         
spacer_poron                                                                                        
#  prop_ID    mat_ID subset_ID               Thick
        17     51115        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 42 12
/PART/42
#part_title                                                                                         
supporter                                                                                           
#  prop_ID    mat_ID subset_ID               Thick
        19     50260        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 58 43
/PART/58
#part_title                                                                                         
cover_rear                                                                                          
#  prop_ID    mat_ID subset_ID               Thick
        21     50220        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 64 37
/PART/64
#part_title                                                                                         
cover_rear_snapfit                                                                                  
#  prop_ID    mat_ID subset_ID               Thick
        23     50220        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 73 11
/PART/73
#part_title                                                                                         
speaker_main_l                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        28     51117        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 74 11
/PART/74
#part_title                                                                                         
speaker_main_r                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        29     51118        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 75 13
/PART/75
#part_title                                                                                         
stand_simple_l                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        30     51119        15                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 76 54
/PART/76
#part_title                                                                                         
stand_simple_r                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        31     51120        15                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 77 26
/PART/77
#part_title                                                                                         
OPENCELL-55-FRIT                                                                                    
#  prop_ID    mat_ID subset_ID               Thick
        33         2        14                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 78 10
/PART/78
#part_title                                                                                         
OPENCELL-55-LCs                                                                                     
#  prop_ID    mat_ID subset_ID               Thick
        34         4        14                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 5 13
/PART/5
#part_title                                                                                         
OPENCELL-55-POL_LOWER                                                                               
#  prop_ID    mat_ID subset_ID               Thick
        35         5        14                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 6 59
/PART/6
#part_title                                                                                         
OPENCELL-55-POL_UPPER                                                                               
#  prop_ID    mat_ID subset_ID               Thick
        35         6        14                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 7 54
/PART/7
#part_title                                                                                         
OPENCELL-55-GLASS_UPPER                                                                             
#  prop_ID    mat_ID subset_ID               Thick
        32         7        14                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 79 12
/PART/79
#part_title                                                                                         
OPENCELL-55-GLASS_LOWER                                                                             
#  prop_ID    mat_ID subset_ID               Thick
        32         8        14                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 80 37
/PART/80
#part_title                                                                                         
WHT_BOX_OUT-6_LEFT-B3                                                                               
#  prop_ID    mat_ID subset_ID               Thick
        36     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 81 13
/PART/81
#part_title                                                                                         
WHT_BOX_OUT-1_REAR-B2                                                                               
#  prop_ID    mat_ID subset_ID               Thick
        37     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 82 26
/PART/82
#part_title                                                                                         
WHT_BOX_OUT-B1                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        38     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 83 59
/PART/83
#part_title                                                                                         
WHT_BOX_OUT-3_FRONT-B4                                                                              
#  prop_ID    mat_ID subset_ID               Thick
        39     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 84 10
/PART/84
#part_title                                                                                         
WHT_BOX_OUT-5_RIGHT-B5                                                                              
#  prop_ID    mat_ID subset_ID               Thick
        40     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 85 54
/PART/85
#part_title                                                                                         
WHT_BOX_OUT-A2                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        41     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 86 12
/PART/86
#part_title                                                                                         
WHT_BOX_OUT-A3                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        42     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 87 9
/PART/87
#part_title                                                                                         
WHT_BOX_OUT-4_TOP-A4                                                                                
#  prop_ID    mat_ID subset_ID               Thick
        43     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 88 60
/PART/88
#part_title                                                                                         
WHT_BOX_OUT-A5                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        44     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 89 11
/PART/89
#part_title                                                                                         
WHT_BOX_OUT-F2                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        45     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 90 43
/PART/90
#part_title                                                                                         
WHT_BOX_OUT-F4                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        46     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 91 59
/PART/91
#part_title                                                                                         
WHT_BOX_OUT-F3                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        47     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 92 59
/PART/92
#part_title                                                                                         
WHT_BOX_OUT-F5                                                                                      
#  prop_ID    mat_ID subset_ID               Thick
        48     51133        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 93 11
/PART/93
#part_title                                                                                         
WHT_BTAPE-A                                                                                         
#  prop_ID    mat_ID subset_ID               Thick
        49     51127        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 94 31
/PART/94
#part_title                                                                                         
WHT_BTAPE-B                                                                                         
#  prop_ID    mat_ID subset_ID               Thick
        50     51127        18                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 98 26
/PART/98
#part_title                                                                                         
frame_middle                                                                                        
#  prop_ID    mat_ID subset_ID               Thick
        53     50230        13                    
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR components 100 59
/PART/100
#part_title                                                                                         
NULL_SHELL_FOR_CONTACT                                                                              
#  prop_ID    mat_ID subset_ID               Thick
        54     51129        13                    
#enddata
/END
