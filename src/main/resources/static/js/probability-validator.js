// 问卷星概率验证器
const ProbabilityValidator = {
    /**
     * 严格解析概率：整串必须都是数字（允许负号，交给范围校验给出准确提示）。
     * parseInt 会把 "50abc" 当成 50、"1e2" 当成 1、"50.5" 当成 50，让非法输入蒙混过关。
     * @param {string} raw 输入框里的原始文本
     * @returns {number|null} 解析结果；不是纯数字时返回 null
     */
    parseProbability: function(raw) {
        const text = String(raw).trim();
        if (!/^-?\d+$/.test(text)) {
            return null;
        }
        const num = Number(text);
        return Number.isSafeInteger(num) ? num : null;
    },

    // 验证所有题目
    validateAll: function() {
        const errors = [];
        
        // 获取所有题目容器
        const questionContainers = document.querySelectorAll('.question-container');
        
        
        if (questionContainers.length === 0) {
            errors.push('未找到题目容器，请确保问卷已正确解析');
            return {
                isValid: false,
                errors: errors
            };
        }
        
        questionContainers.forEach(container => {
            const questionId = container.dataset.questionId;
            const questionType = container.querySelector('.question-type').textContent;
            
            
            let result;
            switch(questionType) {
                case '单选题':
                    result = this.validateSingleChoice(questionId);
                    break;
                case '多选题':
                    result = this.validateMultipleChoice(questionId);
                    break;
                case '填空题':
                    result = this.validateFillBlank(questionId);
                    break;
                case '矩阵题':
                    result = this.validateMatrix(questionId);
                    break;
                case '量表题':
                    result = this.validateScale(questionId);
                    break;
                default:
                    result = { isValid: true, errors: [] };
            }
            
            if (!result.isValid) {
                errors.push(...result.errors);
            }
        });
        
        
        // 检查是否所有验证都通过
        const isValid = errors.length === 0;
        
        if (isValid) {
            // 如果所有验证都通过，移除所有浮动错误提示
            const floatErrors = document.querySelectorAll('.question-error-float');
            floatErrors.forEach(error => {
                error.remove();
            });
            
            // 移除所有题目容器上的错误ID标记
            const questionContainers = document.querySelectorAll('.question-container');
            questionContainers.forEach(container => {
                container.removeAttribute('data-error-id');
            });
        }
        
        return {
            isValid: isValid,
            errors: errors
        };
    },
    
    // 验证单选题
    validateSingleChoice: function(questionId) {
        const errors = [];
        const questionContainer = document.querySelector(`[data-question-id="${questionId}"]`);
        const probabilityInputs = questionContainer.querySelectorAll('.probability-input');
        
        // 收集所有已填写的概率值
        const probabilities = [];
        const validInputs = [];
        
        probabilityInputs.forEach((input, index) => {
            // 先移除可能存在的错误样式
            input.classList.remove('is-invalid');
            input.parentElement.querySelector('.invalid-feedback')?.remove();
            
            const value = input.value.trim();
            if (value) {
                const num = this.parseProbability(value);
                if (num === null) {
                    errors.push(`第${questionId}题（单选题）选项${index + 1}的概率不是有效数字`);
                    this.showInputError(input);
                } else if (num < 0 || num > 100) {
                    errors.push(`第${questionId}题（单选题）选项${index + 1}的概率必须在0-100之间`);
                    this.showInputError(input);
                } else {
                    // 输入正确，确保移除错误样式
                    input.classList.remove('is-invalid');
                    probabilities.push(num);
                    validInputs.push(input);
                }
            }
        });
        
        // 检查是否所有选项都填写了概率
        if (probabilities.length !== probabilityInputs.length) {
            errors.push(`第${questionId}题（单选题）请为所有选项填写概率`);
            // 为未填写的输入框添加错误样式
            probabilityInputs.forEach((input, index) => {
                if (!input.value.trim()) {
                    this.showInputError(input);
                }
            });
        } else {
            // 检查概率总和是否为100%
            const sum = probabilities.reduce((acc, curr) => acc + curr, 0);
            if (sum !== 100) {
                errors.push(`第${questionId}题（单选题）所有选项的概率之和必须为100%，当前总和为${sum}%`);
                // 为所有有效输入框添加错误样式
                validInputs.forEach(input => {
                    input.classList.add('is-invalid');
                });
            } else {
                // 验证通过，确保移除所有错误样式和提示
                validInputs.forEach(input => {
                    input.classList.remove('is-invalid');
                });
                
                // 移除当前题目的所有浮动错误提示
                const questionContainer = document.querySelector(`[data-question-id="${questionId}"]`);
                if (questionContainer) {
                    // 移除通过showQuestionError方法创建的浮动提示
                    const errorId = questionContainer.dataset.errorId;
                    if (errorId) {
                        const floatError = document.getElementById(errorId);
                        if (floatError) {
                            floatError.remove();
                        }
                        questionContainer.removeAttribute('data-error-id');
                    }
                    
                    // 移除通过showErrors方法创建的所有浮动提示
                    const floatErrors = document.querySelectorAll('.question-error-float');
                    floatErrors.forEach(error => {
                        const errorText = error.textContent;
                        if (errorText.includes(`第${questionId}题`)) {
                            error.remove();
                        }
                    });
                }
            }
        }
        
        // 检查是否有带文本输入的选项并验证文本答案配置
        const optionElements = questionContainer.querySelectorAll('.option-item');
        optionElements.forEach((optionElement, optionIndex) => {
            const configButton = optionElement.querySelector('.btn-primary');
            if (configButton && configButton.textContent.includes('编辑文本答案')) {
                // 检查是否已配置文本答案
                const configKey = `${questionId}_${optionIndex}`;
                if (typeof window.textAnswersConfig === 'undefined' || !window.textAnswersConfig[configKey]) {
                    errors.push(`第${questionId}题（单选题）选项${optionIndex + 1}请配置文本答案`);
                }
            }
        });
        
        return {
            isValid: errors.length === 0,
            errors: errors
        };
    },
    
    // 验证多选题
    // 规则与单选题一致（见「系统使用须知」：多选题同单选题）：
    // 每个选项都要填，且概率之和必须为 100%
    validateMultipleChoice: function(questionId) {
        const errors = [];
        const questionContainer = document.querySelector(`[data-question-id="${questionId}"]`);
        const probabilityInputs = questionContainer.querySelectorAll('.probability-input');
        
        // 收集所有已填写的概率值
        const probabilities = [];
        const validInputs = [];
        
        probabilityInputs.forEach((input, index) => {
            // 先移除可能存在的错误样式
            input.classList.remove('is-invalid');
            input.parentElement.querySelector('.invalid-feedback')?.remove();
            
            const value = input.value.trim();
            if (value) {
                const num = this.parseProbability(value);
                if (num === null) {
                    errors.push(`第${questionId}题（多选题）选项${index + 1}的概率不是有效数字`);
                    this.showInputError(input);
                } else if (num < 0 || num > 100) {
                    errors.push(`第${questionId}题（多选题）选项${index + 1}的概率必须在0-100之间`);
                    this.showInputError(input);
                } else {
                    input.classList.remove('is-invalid');
                    probabilities.push(num);
                    validInputs.push(input);
                }
            }
        });
        
        // 检查是否所有选项都填写了概率
        if (probabilities.length !== probabilityInputs.length) {
            errors.push(`第${questionId}题（多选题）请为所有选项填写概率`);
            probabilityInputs.forEach(input => {
                if (!input.value.trim()) {
                    this.showInputError(input);
                }
            });
        } else {
            // 检查概率总和是否为100%
            const sum = probabilities.reduce((acc, curr) => acc + curr, 0);
            if (sum !== 100) {
                errors.push(`第${questionId}题（多选题）所有选项的概率之和必须为100%，当前总和为${sum}%`);
                validInputs.forEach(input => {
                    input.classList.add('is-invalid');
                });
            } else {
                // 验证通过，清掉本题残留的红色横幅
                validInputs.forEach(input => {
                    input.classList.remove('is-invalid');
                });
                this.clearQuestionError(questionContainer);
            }
        }
        
        // 检查是否有带文本输入的选项并验证文本答案配置
        const optionElements = questionContainer.querySelectorAll('.option-item');
        optionElements.forEach((optionElement, optionIndex) => {
            const configButton = optionElement.querySelector('.btn-primary');
            if (configButton && configButton.textContent.includes('编辑文本答案')) {
                // 检查是否已配置文本答案
                const configKey = `${questionId}_${optionIndex}`;
                if (typeof window.textAnswersConfig === 'undefined' || !window.textAnswersConfig[configKey]) {
                    errors.push(`第${questionId}题（多选题）选项${optionIndex + 1}请配置文本答案`);
                }
            }
        });
        
        return {
            isValid: errors.length === 0,
            errors: errors
        };
    },
    
    // 验证填空题
    validateFillBlank: function(questionId) {
        const errors = [];
        const questionContainer = document.querySelector(`[data-question-id="${questionId}"]`);
        
        // 检查是否已配置文本答案
        const configKey = `${questionId}_0`; // 填空题通常只有一个配置项，索引为0
        
        if (typeof window.textAnswersConfig === 'undefined' || !window.textAnswersConfig[configKey]) {
            errors.push(`第${questionId}题（填空题）请配置文本答案`);
        } else {
            // 配置已存在，移除错误提示
            if (questionContainer) {
                // 移除题目级别的错误提示
                const errorId = questionContainer.dataset.errorId;
                if (errorId) {
                    const floatError = document.getElementById(errorId);
                    if (floatError) {
                        floatError.remove();
                    }
                    questionContainer.removeAttribute('data-error-id');
                }
                
                // 移除可能存在的浮动错误提示
                const floatErrors = document.querySelectorAll('.question-error-float');
                floatErrors.forEach(error => {
                    const errorText = error.textContent;
                    if (errorText.includes(`第${questionId}题`)) {
                        error.remove();
                    }
                });
            }
        }
        
        return {
            isValid: errors.length === 0,
            errors: errors
        };
    },
    
    // 验证矩阵题
    validateMatrix: function(questionId) {
        const errors = [];
        const questionContainer = document.querySelector(`[data-question-id="${questionId}"]`);
        const table = questionContainer.querySelector('.matrix-table');
        
        if (!table) {
            errors.push(`第${questionId}题（矩阵题）表格元素未找到`);
            return {
                isValid: false,
                errors: errors
            };
        }
        
        const rows = table.querySelectorAll('tbody tr');
        rows.forEach((row, rowIndex) => {
            const probabilityInputs = row.querySelectorAll('.probability-input');
            
            // 收集当前行所有已填写的概率值
            const probabilities = [];
            const validInputs = [];
            
            probabilityInputs.forEach((input, colIndex) => {
                // 先移除可能存在的错误样式
                input.classList.remove('is-invalid');
                input.parentElement.querySelector('.invalid-feedback')?.remove();
                
                const value = input.value.trim();
                if (value) {
                    const num = this.parseProbability(value);
                    if (num === null) {
                        errors.push(`第${questionId}题（矩阵题）第${rowIndex + 1}行第${colIndex + 1}列的概率不是有效数字`);
                        this.showInputError(input);
                    } else if (num < 0 || num > 100) {
                        errors.push(`第${questionId}题（矩阵题）第${rowIndex + 1}行第${colIndex + 1}列的概率必须在0-100之间`);
                        this.showInputError(input);
                    } else {
                        probabilities.push(num);
                        validInputs.push(input);
                    }
                }
            });
            
            // 检查当前行是否所有选项都填写了概率
            if (probabilities.length !== probabilityInputs.length) {
                errors.push(`第${questionId}题（矩阵题）第${rowIndex + 1}行请为所有选项填写概率`);
                // 为未填写的输入框添加错误样式
                probabilityInputs.forEach((input, colIndex) => {
                    if (!input.value.trim()) {
                        this.showInputError(input);
                    }
                });
            } else {
                // 检查当前行概率总和是否为100%
                const sum = probabilities.reduce((acc, curr) => acc + curr, 0);
                if (sum !== 100) {
                    errors.push(`第${questionId}题（矩阵题）第${rowIndex + 1}行所有选项的概率之和必须为100%，当前总和为${sum}%`);
                    // 为当前行所有有效输入框添加错误样式
                    validInputs.forEach(input => {
                        input.classList.add('is-invalid');
                    });
                } else {
                    // 验证通过，移除所有错误样式和提示
                    validInputs.forEach(input => {
                        input.classList.remove('is-invalid');
                    });
                    
                    // 移除当前题目的所有浮动错误提示
                    if (questionContainer) {
                        // 移除通过showQuestionError方法创建的浮动提示
                        const errorId = questionContainer.dataset.errorId;
                        if (errorId) {
                            const floatError = document.getElementById(errorId);
                            if (floatError) {
                                floatError.remove();
                            }
                            questionContainer.removeAttribute('data-error-id');
                        }
                        
                        // 移除通过showErrors方法创建的所有浮动提示
                        const floatErrors = document.querySelectorAll('.question-error-float');
                        floatErrors.forEach(error => {
                            const errorText = error.textContent;
                            if (errorText.includes(`第${questionId}题`)) {
                                error.remove();
                            }
                        });
                    }
                }
            }
        });
        
        return {
            isValid: errors.length === 0,
            errors: errors
        };
    },
    
    // 验证量表题
    validateScale: function(questionId) {
        const errors = [];
        const questionContainer = document.querySelector(`[data-question-id="${questionId}"]`);
        const probabilityInputs = questionContainer.querySelectorAll('.probability-input');
        
        // 收集所有已填写的概率值
        const probabilities = [];
        const validInputs = [];
        
        probabilityInputs.forEach((input, index) => {
            // 先移除可能存在的错误样式
            input.classList.remove('is-invalid');
            input.parentElement.querySelector('.invalid-feedback')?.remove();
            
            const value = input.value.trim();
            if (value) {
                const num = this.parseProbability(value);
                if (num === null) {
                    errors.push(`第${questionId}题（量表题）选项${index + 1}的概率不是有效数字`);
                    this.showInputError(input);
                } else if (num < 0 || num > 100) {
                    errors.push(`第${questionId}题（量表题）选项${index + 1}的概率必须在0-100之间`);
                    this.showInputError(input);
                } else {
                    probabilities.push(num);
                    validInputs.push(input);
                }
            }
        });
        
        // 检查是否所有选项都填写了概率
        if (probabilities.length !== probabilityInputs.length) {
            errors.push(`第${questionId}题（量表题）请为所有选项填写概率`);
            // 为未填写的输入框添加错误样式
            probabilityInputs.forEach((input, index) => {
                if (!input.value.trim()) {
                    this.showInputError(input, '');
                }
            });
        } else {
            // 检查概率总和是否为100%
            const sum = probabilities.reduce((acc, curr) => acc + curr, 0);
            if (sum !== 100) {
                errors.push(`第${questionId}题（量表题）所有选项的概率之和必须为100%，当前总和为${sum}%`);
                // 为所有有效输入框添加错误样式
                validInputs.forEach(input => {
                    input.classList.add('is-invalid');
                });
            } else {
                // 验证通过，移除所有错误样式和提示
                validInputs.forEach(input => {
                    input.classList.remove('is-invalid');
                });
                
                // 移除当前题目的所有浮动错误提示
                if (questionContainer) {
                    // 移除通过showQuestionError方法创建的浮动提示
                    const errorId = questionContainer.dataset.errorId;
                    if (errorId) {
                        const floatError = document.getElementById(errorId);
                        if (floatError) {
                            floatError.remove();
                        }
                        questionContainer.removeAttribute('data-error-id');
                    }
                    
                    // 移除通过showErrors方法创建的所有浮动提示
                    const floatErrors = document.querySelectorAll('.question-error-float');
                    floatErrors.forEach(error => {
                        const errorText = error.textContent;
                        if (errorText.includes(`第${questionId}题`)) {
                            error.remove();
                        }
                    });
                }
            }
        }
        
        return {
            isValid: errors.length === 0,
            errors: errors
        };
    },
    
    // 验证文本答案
    validateTextAnswers: function(textAnswers) {
        const errors = [];
        
        if (textAnswers.length === 0) {
            errors.push('请至少添加一个文本答案');
            return {
                isValid: false,
                errors: errors
            };
        }
        
        // 获取文本答案弹窗中的所有输入框
        const answerItems = document.querySelectorAll('#answerList .answer-item');
        
        // 收集所有已填写的概率值
        const probabilities = [];
        
        answerItems.forEach((item, index) => {
            const textInput = item.querySelector('.answer-text');
            const probabilityInput = item.querySelector('.probability-input');
            
            // 先移除可能存在的错误样式
            textInput.classList.remove('is-invalid');
            probabilityInput.classList.remove('is-invalid');
            item.querySelectorAll('.invalid-feedback').forEach(el => el.remove());
            
            // 检查文本输入
            if (textInput.value.trim() === '') {
                errors.push(`第${index + 1}个文本答案不能为空`);
                this.showInputError(textInput);
            }
            
            // 检查概率输入
            if (probabilityInput.value.trim() === '') {
                errors.push(`第${index + 1}个文本答案的概率不能为空`);
                this.showInputError(probabilityInput);
            } else {
                const num = this.parseProbability(probabilityInput.value);
                if (num === null) {
                    errors.push(`第${index + 1}个文本答案的概率不是有效数字`);
                    this.showInputError(probabilityInput);
                } else if (num < 0 || num > 100) {
                    errors.push(`第${index + 1}个文本答案的概率必须在0-100之间`);
                    this.showInputError(probabilityInput, '');
                } else {
                    probabilities.push(num);
                }
            }
        });
        
        // 检查概率总和是否为100%
        if (probabilities.length === answerItems.length) {
            const sum = probabilities.reduce((acc, curr) => acc + curr, 0);
            if (sum !== 100) {
                errors.push(`文本答案的概率之和必须为100%，当前总和为${sum}%`);
                // 为所有概率输入框添加错误样式
                answerItems.forEach(item => {
                    const probabilityInput = item.querySelector('.probability-input');
                    if (probabilityInput.value.trim()) {
                        probabilityInput.classList.add('is-invalid');
                    }
                });
            } else {
                // 验证通过，移除所有错误样式
                answerItems.forEach(item => {
                    const textInput = item.querySelector('.answer-text');
                    const probabilityInput = item.querySelector('.probability-input');
                    textInput.classList.remove('is-invalid');
                    probabilityInput.classList.remove('is-invalid');
                });
            }
        }
        
        return {
            isValid: errors.length === 0,
            errors: errors
        };
    },
    
    // 显示错误信息（统一走右上角提示）
    showErrors: function(errors) {
        if (!errors || errors.length === 0) return;
        this.showQuestionLevelErrors({ isValid: false, errors: errors });
    },
    
    // 显示题目级别的错误信息：每个出错题目挂一条红色浮标 + 汇总一条右上角通知
    showQuestionLevelErrors: function(validationResult) {
        if (validationResult.isValid) return;
        
        const errors = validationResult.errors || [];
        if (errors.length === 0) return;
        
        // 清掉上一轮的浮标
        document.querySelectorAll('.question-error-float').forEach(el => el.remove());
        
        // 逐题挂浮标（浮标文本本身带「第X题」，便于其它逻辑按题目清理）
        document.querySelectorAll('.question-container').forEach(container => {
            const questionId = container.dataset.questionId;
            if (!questionId) return;
            const hit = errors.filter(error => error.includes(`第${questionId}题`));
            if (hit.length > 0) {
                this.showQuestionError(container, hit[0]);
            }
        });
        
        // 再汇总一条右上角通知
        if (typeof showAlert === 'function') {
            const message = errors.length > 1
                ? `${errors[0]}（另有 ${errors.length - 1} 处问题）`
                : errors[0];
            showAlert(message, 'error');
        }
    },
    
    // 显示单个输入框的错误信息 - 只添加红色边框，不显示嵌入的文本提示
    showInputError: function(input) {
        // 只添加错误样式（红色边框），不创建嵌入的错误提示文本
        input.classList.add('is-invalid');
    },
    
    // 移除某一题的红色横幅（题目改对后调用）
    clearQuestionError: function(questionContainer) {
        if (!questionContainer) return;
        const questionId = questionContainer.dataset.questionId;
        
        // 当前实现：横幅在卡片内部
        questionContainer.querySelectorAll('.question-error-float').forEach(el => el.remove());
        
        // 兜底：早期挂在 body 上的浮动块
        if (questionId) {
            document.querySelectorAll('.question-error-float').forEach(el => {
                if (el.dataset.for === questionId) el.remove();
            });
        }
        
        questionContainer.removeAttribute('data-error-id');
    },
    
    // 显示题目级别的错误信息：在题目卡片顶部插一条红色横幅
    // 横幅在卡片内部、文档流里，所以既不会盖住别的题目，也不会压住右上角通知或弹窗
    showQuestionError: function(questionContainer, message) {
        if (!questionContainer) return;
        
        // 同题只留一条
        this.clearQuestionError(questionContainer);
        
        const errorDiv = document.createElement('div');
        errorDiv.className = 'question-error-float';
        
        const questionId = questionContainer.dataset.questionId;
        if (questionId) errorDiv.dataset.for = questionId;
        errorDiv.textContent = message;
        
        questionContainer.insertBefore(errorDiv, questionContainer.firstChild);
    },
    
    // 为所有概率输入框添加实时验证
    addRealTimeValidation: function() {
        // 为所有概率输入框添加实时验证
        const probabilityInputs = document.querySelectorAll('.probability-input');
        
        probabilityInputs.forEach(input => {
            // 移除之前的事件监听器
            const newInput = input.cloneNode(true);
            input.parentNode.replaceChild(newInput, input);
            
            // 为新输入框添加实时验证（输入时只验证单个输入框）
            newInput.addEventListener('input', () => {
                this.validateSingleInput(newInput);
            });
            
            // 当输入框失去焦点时，验证整个题目
            newInput.addEventListener('blur', () => {
                this.validateCurrentQuestion(newInput);
            });
        });
        
        // 开刷按钮的校验统一交给 analysis.js 的 brushBtn 逻辑（内部调用 showErrors），
        // 这里不再重复绑定，避免同一次点击弹出两条提示
    },
    
    // 验证单个输入框
    validateSingleInput: function(input) {
        // 移除之前的错误样式
        input.classList.remove('is-invalid');
        
        // 验证当前输入值
        const value = input.value.trim();
        if (value) {
            const num = this.parseProbability(value);
            if (num === null) {
                this.showInputError(input, '');
            } else if (num < 0) {
                this.showInputError(input, '');
            } else if (num > 100) {
                this.showInputError(input, '');
            }
        }
    },
    
    // 验证当前输入框所属的题目
    validateCurrentQuestion: function(input) {
        // 找到当前输入框所属的题目
        const questionContainer = input.closest('.question-container');
        if (!questionContainer) return;
        
        const questionId = questionContainer.dataset.questionId;
        const questionType = questionContainer.querySelector('.question-type').textContent;
        
        // 先清掉本题上一轮的浮标，题目改对后就不会留下红色残留
        this.clearQuestionError(questionContainer);
        
        // 针对不同题型进行验证
        let result;
        switch(questionType) {
            case '单选题':
                result = this.validateSingleChoice(questionId);
                break;
            case '多选题':
                result = this.validateMultipleChoice(questionId);
                break;
            case '填空题':
                result = this.validateFillBlank(questionId);
                break;
            case '矩阵题':
                result = this.validateMatrix(questionId);
                break;
            case '量表题':
                result = this.validateScale(questionId);
                break;
            default:
                result = { isValid: true, errors: [] };
        }
        
        // 显示题目级别的错误信息
        if (!result.isValid) {
            this.showQuestionError(questionContainer, result.errors[0]);
        }
    },
    
    // 为文本答案配置弹窗的保存按钮添加验证
    addTextAnswerValidation: function() {
        const saveTextAnswersBtn = document.querySelector('#textAnswerModal .btn-primary');
        if (saveTextAnswersBtn) {
            saveTextAnswersBtn.addEventListener('click', (e) => {
                // 收集当前文本答案
                const answerList = document.getElementById('answerList');
                const answerItems = answerList.querySelectorAll('.answer-item');
                
                const textAnswers = [];
                answerItems.forEach(item => {
                    const textInput = item.querySelector('.answer-text');
                    const probabilityInput = item.querySelector('.probability-input');
                    
                    if (textInput.value.trim() && probabilityInput.value.trim()) {
                        textAnswers.push({
                            text: textInput.value.trim(),
                            probability: probabilityInput.value.trim()
                        });
                    }
                });
                
                // 验证文本答案
                const result = this.validateTextAnswers(textAnswers);
                if (!result.isValid) {
                    e.preventDefault();
                    e.stopPropagation();
                    this.showErrors(result.errors);
                }
            });
        }
    }
};

// 将ProbabilityValidator对象暴露到全局作用域
window.ProbabilityValidator = ProbabilityValidator;

// 确保在页面完全加载后初始化验证功能
function initializeValidation() {
    // 不需要在这里调用addRealTimeValidation，因为此时题目还没有动态生成
    // 只需要在questionsLoaded事件触发时调用即可
}

// 为动态加载的题目添加验证
window.addEventListener('questionsLoaded', function() {
    ProbabilityValidator.addRealTimeValidation();
});

// 初始化验证功能
initializeValidation();