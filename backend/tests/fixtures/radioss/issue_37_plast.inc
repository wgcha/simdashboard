#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR materials 7 37
/MAT/PLAS_JOHNS/7
MAT-OPENCELL-GLASS_UPPER                                                                            
#              RHO_I
2.50000000000000E-09
#                  E                  Nu     Iflag        VP
             60000.0                0.23         1          
#              SIG_Y                 UTS                EUTS           EPS_p_max            SIG_max0
              1000.0              1200.0               0.015                 0.5              1200.0
#                  c           EPS_DOT_0       ICC   Fsmooth               F_cut               Chard
                                                                                                    
#                  m              T_melt              rhoC_p                 T_r
                                                                                
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR materials 50130 59
/MAT/PLAS_TAB/50130
PS_DP_2.2t_M36_MD_75QM80F                                                                           
#        Init. dens.
1.19000000000000E-09
#                  E                  Nu           Eps_p_max               Eps_t               Eps_m
             3240.47                0.39                                                            
#  N_funct  F_smooth              C_hard               F_cut               Eps_f                  VP
         1                                                                                          
#  fct_IDp              Fscale   Fct_IDE                EInf                  CE
         0                                                                      
# func_ID1  func_ID2  func_ID3  func_ID4  func_ID5
     60030
#           Fscale_1            Fscale_2            Fscale_3            Fscale_4            Fscale_5
                 0.0
#          Eps_dot_1           Eps_dot_2           Eps_dot_3           Eps_dot_4           Eps_dot_5
                 0.0
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR curves 60030 43
/FUNCT/60030
#title                                                                                              
PS_75QM80F_2.2t_MD                                                                                  
#                  X                   Y
                 0.0                21.6
1.42000000000000E-04                25.6
2.85000000000000E-04                29.0
4.27000000000000E-04                31.9
5.70000000000000E-04                34.4
7.12000000000000E-04                36.6
8.54000000000000E-04                38.5
9.97000000000000E-04                40.2
             0.00114                41.6
             0.00128                42.6
             0.00142                43.1
             0.00157                43.2
             0.00171                43.3
                 0.1                44.0
                 0.5                45.0
                 1.0                46.0
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR materials 5 59
/MAT/ELAST/5
MAT-OPENCELL-POL_LOWER                                                                              
#              RHO_I
2.80000000000000E-09
#                  E                  nu
              1150.0                0.23
#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|
##HWCOLOR materials 4 26
/MAT/LAW93/4
MAT-OPENCELL-LC                                                                                     
#              Rho_i
1.00000000000000E-09
#                E11                 E22                 E33                 G12                NU12
&LC_E               &LC_E               &LC_E_Z             &LC_G                                0.0
#                G13                 G23                NU13                NU23
&LC_G               &LC_G                                0.0                 0.0
#       NL        VP                Fcut
                                        
#            sigma_y                 QR1                 CR1                 QR2                 CR2
                                                                                                    
#                R11                 R22                 R12
                                                            
#                R33                 R13                 R23
                                                            