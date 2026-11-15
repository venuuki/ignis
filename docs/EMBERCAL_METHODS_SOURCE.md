# EmberCal calibration methods

The full current methods, model validation, variance calculations, input cleaning, external APIs, live feed and user workflows are documented in [EmberCal Calibration and Data Methods](EmberCal_Calibration_and_Data_Methods.docx). The implementation protocol is summarized in [Harmonization v3](HARMONIZATION_V3.md).

The historical activity series is calibrated MODIS on the VIIRS S-NPP-equivalent scale for every available month. Its identifier is `modis_harmonized`. Native MODIS observations, native S-NPP observations and the gap-filled VIIRS reference are separate comparison series.

For the main production model, a cell-month prediction equals 3.438716036645814 times daytime MODIS detections plus 8.618368959445155 times nighttime MODIS detections. The Terra-only period before July 2002 uses the separately fitted LightGBM model. Selected-area counts use fractional cell-area weights; model variance uses squared weights and calibrated correlation inflation.

Training years are 2013, 2016, 2018 and 2023; calibration years are 2017 and 2022; final-test years are 2019, 2020 and 2021. Production refitting follows frozen evaluation. Final-test national yearly absolute percentage error is 5.58%, monthly MAPE 11.65%, signed bias -4.37%, and nominal 80% country interval coverage 72.22%. These quantify agreement with the satellite reference, not ground-truth fire accuracy.

Forecasts are trained on calibrated MODIS history. Their report records `target_series`, source build identity and rolling-origin skill. The independent NASA FIRMS live layer displays native detections and does not substitute for monthly calibrated history.
